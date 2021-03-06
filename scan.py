#! /usr/bin/python3
# python = 3.4.2

import os
from sys import exit
from platform import node
from datetime import datetime
from subprocess import Popen, PIPE, STDOUT, CalledProcessError
from argparse import ArgumentParser
from jinja2 import Template, FileSystemLoader, Environment
from smtplib import SMTP
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from time import sleep
import io
import configparser
import json
import hashlib
from virus_total_apis import PublicApi as VirusTotalPublicApi
from logger import Logger

logger = Logger()
config = configparser.ConfigParser()
config.read('config.ini')


def send_mail(html, recipient, subject):
    """

    Sends email with report

    """

    user = str(config['Main']['EmailUser'])
    password = str(config['Main']['EmailPassword'])
    header_from = str(config['Main']['HeaderFrom'])

    msg = MIMEMultipart()
    msg['From'] = user
    msg['To'] = recipient
    msg['Subject'] = subject

    msg.attach(MIMEText(html, 'html'))

    report_attached = MIMEBase('application', 'octet-stream')
    report_attached.set_payload(html)
    encoders.encode_base64(report_attached)
    report_attached.add_header('Content-Disposition', "attachment; filename = report.html")
    msg.attach(report_attached)

    client = SMTP(host='smtp.office365.com', port=587)
    client.starttls()
    client.login(user, password)

    logger.add("Sending e-mail to {0}".format(recipient))
    client.sendmail(header_from, recipient, msg.as_string())
    logger.add("E-mail sent")
    client.quit()


def parse_clamav(parsed_args_p):
    """
    Launches ClamAV and returns its output in form [ [path,virusname] ]
    """

    logger.add("-" * 10)
    logger.add("Starting scan...")

    data = []
    data2 = []

    try:
        for line in Popen(['clamscan', '-r', '-i', '--no-summary', '{}'.format(parsed_args_p.dir)],
                          stdout=PIPE).communicate():
            if line:

                data = line.decode('utf-8').split('FOUND\n')[:-1]

                for elem in data[:]:
                    elem = elem.split(':')
                    data2.append(elem)
            data = data2

    except OSError:
        print("Please check ClamAV is installed or present in PATH")
        exit(1)

    if not data:
        exit(0)

    logger.add("Gathered data: {0}".format(data))
    check_all_files_on_virustotal(data)

    return data


def process_whitelist(data, white_list = 'whitelist.txt'):
    """
    Removes everything in whitelist from data
    """

    if not os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), white_list)):
        open(os.path.join(os.path.dirname(os.path.abspath(__file__)), white_list), 'a').close()

    data = [element for element in data if element[0] not in
            [l.strip() for l in
             open(os.path.join(os.path.dirname(os.path.abspath(__file__)), white_list), 'r').readlines()]]
    logger.add("After whitelist: {0}".format(data))

    return data


def add_to_whitelist(file, white_list='whitelist.txt'):
    """
    Checks if line is present in whitelist and adds the file otherwise
    """

    logger.add("Adding to whitelist")

    if not os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(__file__)), white_list)):
        open(os.path.join(os.path.dirname(os.path.abspath(__file__)), white_list), 'a').close()

    if file not in [line.strip() for line in
                    open(os.path.join(os.path.dirname(os.path.abspath(__file__)), white_list), 'r').readlines()]:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), white_list), 'a') as whitelist:
            whitelist.write(file + "\n")


def open_binary_file(path_to_file):
    """
    Returns first and last 32000 bytes from file
    """

    f = open(path_to_file, 'rb')
    return f.read(32000), f.read(f.seek(os.path.getsize(path_to_file) - 32000))


def send_request(file, from_disk, original_path):
    logger.add(" " * 10)
    logger.add("File {0}".format(original_path))
    api_key = str(config['Main']['ApiKey'])
    vt = VirusTotalPublicApi(api_key)

    scanned_file = {'response_code': 000}
    report = {'response_code': 000}

    while scanned_file['response_code'] != 200:

        scanned_file = json.loads(
            json.dumps(vt.scan_file(this_file=file, from_disk=from_disk), sort_keys=True, indent=4))

        try:
            logger.add("Response: scan_file: {0}".format(scanned_file['results']['verbose_msg']))
        except KeyError:
            logger.add("Error in scan_file: {0}".format(scanned_file))
            pass

        while report['response_code'] != 200:

            report = vt.get_file_report(scanned_file['results']['resource'])

            if report['response_code'] == 204:
                logger.add("Sleeping for 15 seconds: {0}".format(report))
                sleep(15)
                continue

            try:
                logger.add("Response: get_file_report: {0} & {1}".format(report['response_code'],
                                                                         report['results']['response_code']))
            except KeyError:
                logger.add("Error in get_file_report: {0}".format(report))
                pass
            if report['response_code'] == 200 and report['results']['response_code'] != 1:
                logger.add("API bad Response. File lost in queue, rescanning")
                check_one_file_on_virustotal([file])
                break
            try:
                ratio = round(report['results']['positives'] / report['results']['total'] * 100, 2)
                logger.add(
                    "Positives: {0} , Total {1}".format(report['results']['positives'], report['results']['total']))
                logger.add("Ratio is: {0}".format(ratio))

                if ratio < 85.0:
                    add_to_whitelist(original_path)
            except KeyError:
                pass
    return 0


def check_one_file_on_virustotal(file):
    """
    Sends file to virustotal if file < 32MB
    Else sends first 32MB and last 32MB
    """

    file = file[0]

    if os.path.getsize(file) > 32000000:
        for chunk in open_binary_file(file):
            send_request(file=chunk, from_disk=False, original_path=file)
    else:
        send_request(file=file, from_disk=True, original_path=file)


def check_all_files_on_virustotal(data):
    if data:
        [check_one_file_on_virustotal(file) for file in [elem for elem in data]]


def form_template(data, parsed_args_t, host_t, now_t, time_end):
    """
    Forms html template from initial data
    """

    loader = FileSystemLoader(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'))
    env = Environment(loader=loader, trim_blocks=True, lstrip_blocks=True)
    template = env.get_template('report.tmpl')

    rendered_html = (template.render(data={
        'data': data,
        'time': now_t,
        'dir': parsed_args_t.dir,
        'host': host_t,
        'time_end': time_end
    }))

    return rendered_html


if __name__ == "__main__":
    parser = ArgumentParser(description="ClamAV Scanner. Example: ./scan.py '/root' 'billgates@microsoft.com' ")
    parser.add_argument('dir', type=str, help='Directory to scan')
    parser.add_argument('sendto', type=str, help='Report recipient address')
    parsed_args = parser.parse_args()

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    host = node()

    send_mail(form_template(
        process_whitelist(parse_clamav(parsed_args)), parsed_args, host, now,
        datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    ), recipient=parsed_args.sendto, subject="[{0}] ClamAV: scanned {1} on {2}".format(now, parsed_args.dir, host))
