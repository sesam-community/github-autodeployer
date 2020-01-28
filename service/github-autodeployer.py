#!/usr/bin/env python3

import os
import requests
import shutil
import zipfile
import glob
import filecmp
import os.path
from git import Repo
import fnmatch
import logging
import datetime
from json import loads as load_json, dumps as dump_json
from re import findall as regex_findall
from Vaulter import Vaulter

__author__ = "Enrico Razzetti"

sesam_api = os.environ.get('SESAM_API_URL', 'http://sesam-node:9042/api')  # ex: "https://abcd1234.sesam.cloud/api"
jwt = os.environ.get('JWT')
git_repo = os.environ.get('GIT_REPO')  # the project you want to sync from
branch = os.environ.get('BRANCH', 'master')  # the branch of the project you want to use for a sync
sync_root = os.environ.get('SYNC_ROOT', '/')  # the top directory from the github repo you want to use for sync
deploy_token = os.environ.get('DEPLOY_TOKEN')  # ssh deploy key for this particular project
autodeployer_config_path = os.environ.get('AUTODEPLOYER_PATH')  # path to system config in current node config
git_username = os.environ.get('GIT_USERNAME', None)  # Needed if using clone_git_repov3
var_file_path: str = os.environ.get('VARIABLES_FILE_PATH')
vault_git_token = os.environ.get('VAULT_GIT_TOKEN')
vault_mounting_point = os.environ.get('VAULT_MOUNTING_POINT')
vault_url = os.environ.get('VAULT_URL')
## internal, skeleton, don't touch, you perv! *touchy, touchy*

git_cloned_dir = "/tmp/git_upstream_clone"
sesam_checkout_dir = "/tmp/sesam_conf"
zipped_payload = "/tmp/payload/sesam.zip"
payload_dir = "/tmp/payload"

# set logging
log_level = logging.getLevelName(os.environ.get('LOG_LEVEL', 'INFO'))  # default log level = INFO
logging.basicConfig(level=log_level)  # dump log to stdout

logging.info(datetime.datetime.now())
logging.debug("Github repo: %s" % git_repo)
logging.debug("Branch: %s" % branch)
logging.debug("Sync root: %s" % sync_root)
logging.debug("Target sesam instance: %s" % sesam_api)

if git_username is None:
    logging.critical('GIT_USERNAME env-var missing! Exiting.')
    exit(-1)

## remove a directory if it exists
def remove_if_exists(path):
    if os.path.exists(path):
        for root, dirs, files in os.walk(path):
            # os.remove(path)
            shutil.rmtree(path)


def clone_git_repov3():
    remove_if_exists(git_cloned_dir)
    url = f'https://{git_username}:{deploy_token}@{git_repo.split("@")[-1].replace(":", "/")}'
    repo = Repo.clone_from(url, git_cloned_dir, branch=branch)
    return repo


## remove .git, .gitignore and README from a cloned github repo directory
def clean_git_repo():
    # os.chdir(git_cloned_dir)
    for path in glob.glob(git_cloned_dir + "/" + '.git'):
        shutil.rmtree(path)
    for path in glob.glob(git_cloned_dir + "/" + '.gitignore'):
        os.remove(path)
    for path in glob.glob(git_cloned_dir + "/" + 'README.md'):
        os.remove(path)


## zip a directory
def zip_payload():
    logging.info("removing old config " + zipped_payload)
    remove_if_exists(zipped_payload)
    logging.debug("removed")
    logging.debug("payload dir: " + payload_dir)
    logging.info('Zipping new config')
    with zipfile.ZipFile(zipped_payload, 'w', zipfile.ZIP_DEFLATED) as zippit:
        os.chdir(payload_dir)
        for file in glob.glob('**', recursive=True):
            if os.path.isfile(file) and file != "sesam.zip":
                logging.debug(file)
                zippit.write(file)


## create a directory
def create_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)


def do_put(ses, url, json, params=None):
    retries = 4
    try:
        for tries in range(retries):
            request = ses.put(url=url, json=json, params=params)
            if request.ok:
                logging.info(f'Succesfully PUT request to "{url}"')
                return 0
            else:
                logging.warning(
                    f'Could not PUT request to url "{url}". Got response {request.content}. Current try {tries} of {retries}')
        logging.error(f'Each PUT request failed to "{url}".')
        return -1
    except Exception as e:
        logging.error(f'Got exception "{e}" while doing PUT request to url "{url}"')
        return -2


def verify_node(node):
    node_string = dump_json(node)

    variables_in_conf = regex_findall(r'\$ENV\((\S*?)\)', node_string)  # Find env vars
    variables: dict = load_json(open(git_cloned_dir + sync_root + 'node/' + var_file_path).read())
    for var in variables_in_conf:  # Verify they exist in git repo
        if var not in variables:
            logging.error(f'Missing env var {var} in variables file {var_file_path}')

    secrets_in_conf = regex_findall(r'\$SECRET\((\S*?)\)', node_string)  # Find secrets
    vault = Vaulter(vault_url, vault_git_token, vault_mounting_point)  # Create keyvault object
    secrets: dict = vault.get_secrets(secrets_in_conf)  # Get the secrets from keyvault
    if vault.verify() is False:  # Verify all secrets exist.
        logging.error(f'These secrets do not exist in the vault {vault.get_missing_secrets()}')
    return variables, secrets


def load_sesam_files_as_json(dir):
    node_config = []
    for name in os.listdir(dir):
        path = os.path.join(dir, name)
        if os.path.isfile(path) and fnmatch.fnmatch(name, 'node-metadata.conf.json'):
            node_config.append(load_json(open(path).read()))
        elif os.path.isdir(path):
            if fnmatch.fnmatch(name, 'pipes') or fnmatch.fnmatch(name, 'systems'):
                pipes_or_systems = os.listdir(path)
                for p_s in pipes_or_systems:
                    local_path = os.path.join(path, p_s)
                    node_config.append(load_json(open(local_path).read()))
    return node_config


def compare_json_dict_list(list1, list2):
    sorted1 = sorted(list1, key=lambda i: i['_id'])

    sorted2 = sorted(list2, key=lambda i: i['_id'])
    return sorted1 == sorted2


## match the sesam configuration files and copy them to the payload directory
def extract_sesam_files_from(dir):
    for name in os.listdir(dir):
        path = os.path.join(dir, name)
        if os.path.isfile(path):
            if fnmatch.fnmatch(name, 'node-metadata.conf.json'):
                shutil.copyfile(path, payload_dir + "/" + name)

        #            elif fnmatch.fnmatch(name, 'test-env.json'):
        #                shutil.copyfile(path, payload_dir+"/"+name)
        else:
            extract_sesam_files_from(path)
            if os.path.isdir(path):
                if fnmatch.fnmatch(name, 'pipes'):
                    shutil.copytree(path, payload_dir + "/" + name)
                elif fnmatch.fnmatch(name, 'systems'):
                    shutil.copytree(path, payload_dir + "/" + name)


def prepare_payload():
    remove_if_exists(payload_dir)
    create_dir(payload_dir)
    extract_sesam_files_from(git_cloned_dir + "/" + sync_root)


## download the sesam configuration
def download_sesam_zip():
    remove_if_exists(sesam_checkout_dir)
    create_dir(sesam_checkout_dir)
    request = requests.get(url=sesam_api + "/config",
                           headers={'Accept': 'application/zip', 'Authorization': 'bearer ' + jwt})
    if request.status_code == 200:
        logging.info("OK, the Sesam api answered with status code: %s" % request.status_code)
        with open(sesam_checkout_dir + "/" + "sesam.zip", 'wb') as f:
            for chunk in request.iter_content(1024):
                f.write(chunk)
    else:
        logging.error("Non 200 status code from the Sesam api, got: %s" % request.status_code)


## upload the sesam configuration straight from the cloned git repo
def upload_payload():
    logging.debug('hvor er jeg?' + os.getcwd())
    request = requests.put(url=sesam_api + "/config?force=true",
                           data=open(zipped_payload, 'rb').read(),
                           headers={'Content-Type': 'application/zip', 'Authorization': 'bearer ' + jwt})
    if request.status_code == 200:
        logging.info("OK. The Sesam api answered with status code: %s" % request.status_code)
    else:
        logging.error("Non 200 status code from the Sesam api, got: %s" % request.status_code)


## unzip the downloaded sesam zip archive
def unpack_sesam_zip():
    remove_if_exists(sesam_checkout_dir + "/" + "unpacked")
    create_dir(sesam_checkout_dir + "/" + "unpacked")
    zip_ref = zipfile.ZipFile(sesam_checkout_dir + "/" + "sesam.zip", 'r')
    zip_ref.extractall(sesam_checkout_dir + "/" + "unpacked")
    zip_ref.close()


def copy_autodeployer():
    start_path = sesam_checkout_dir + "/" + "unpacked/" + autodeployer_config_path
    target_path = payload_dir + "/" + autodeployer_config_path
    shutil.copyfile(start_path, target_path)


## check that there is no error in the downloaded zip.
## we observed that if the downloaded archive from archive
## contains a directory called "unknown" probably a json file
## with data that does not belong to, say pipes, has been added there.
## nice to raise a flag then.

def check_for_unknown():
    if os.path.exists(sesam_checkout_dir + "/" + "unpacked" + "/unknown"):
        logging.warning("\n")
        logging.warning("WARNING:")
        logging.warning("Looks like Sesam has flagged some of your github committed data as gibberish:")
        logging.warning("I detected a directory called 'unknown' in the dowloaded configuration from the node.")
        logging.warning(
            "This could be, for example, some data file added to the pipes directory. But i don't know for sure.")
        logging.warning(
            "This error is in your github committed code and should be corrected before continuing with your workflow,")
        logging.warning("else, prepare for unexpected behaviour. Hic Sunt Leones. You have been warned.")
        logging.warning("\n")


if __name__ == '__main__':
    ## we first clone the repo, clean it up, and extract the relevant files to prepare the payload.
    clone_git_repov3()
    clean_git_repo()
    prepare_payload()
    ## we then download the sesam configuration from the api, unpack it, check it ...
    download_sesam_zip()
    unpack_sesam_zip()
    check_for_unknown()
    copy_autodeployer()

    new_node = load_sesam_files_as_json(git_cloned_dir + "/" + sync_root + '/node')
    old_node = load_sesam_files_as_json(sesam_checkout_dir + "/" + "unpacked")
    if not compare_json_dict_list(old_node, new_node):
        # Verify variables & secrets
        variables, secrets = verify_node(new_node)

        logging.debug(f'Uploading secrets & variables to node!')
        # Upload variables & secrets
        session = requests.session()
        session.headers = {'Authorization': f'bearer {jwt}'}
        if do_put(session, f'{sesam_api}/secrets', json=secrets) != 0 or do_put(session, f'{sesam_api}/env', json=variables) != 0:
            logging.error('Failed to upload secrets or variables to node!')
            exit(-1)
        logging.info(f"Uploading new configuration from github to node {sesam_api}")
        zip_payload()
        upload_payload()
    else:
        logging.info("No change, doing nothing.")
