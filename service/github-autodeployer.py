#!/usr/bin/env python3

import datetime
import fnmatch
import glob
import logging
import os
import os.path
import shutil
import zipfile
from json import loads as load_json, dumps as dump_json
from re import findall as regex_findall
from time import sleep

import requests
from Vaulter import Vaulter
from git import Repo
from git.remote import to_progress_instance

__author__ = "Enrico Razzetti"

sesam_api = os.environ.get('SESAM_API_URL', 'http://sesam-node:9042/api')  # ex: "https://abcd1234.sesam.cloud/api"
jwt = os.environ.get('JWT')
git_repo = os.environ.get('GIT_REPO')  # the project you want to sync from
branch = os.environ.get('BRANCH', 'master')  # the branch of the project you want to use for a sync
tag = os.environ.get('TAG')
sync_root = os.environ.get('SYNC_ROOT', '/')  # the top directory from the github repo you want to use for sync
deploy_token = os.environ.get('DEPLOY_TOKEN')  # ssh deploy key for this particular project
autodeployer_config_path = os.environ.get('AUTODEPLOYER_PATH')  # path to system config in current node config
var_file_path: str = os.environ.get('VARIABLES_FILE_PATH')
vault_git_token = os.environ.get('VAULT_GIT_TOKEN')
vault_mounting_point = os.environ.get('VAULT_MOUNTING_POINT')
vault_url = os.environ.get('VAULT_URL')
vault_path_prefix = os.environ.get('VAULT_PATH_PREFIX', "")
orchestrator = os.environ.get('ORCHESTRATOR', False)
verify_ssl = os.environ.get('VERIFY_SSL', False)

git_username = os.environ.get('GIT_USERNAME', None)  # Needed if using clone_git_repov3

turn_off = os.environ.get('off', 'false').lower() == 'true'

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

try:
    sleep_interval = int(os.environ.get("SLEEP_INTERVAL", 60))
except (ValueError, TypeError):
    logging.warning('SLEEP_INTERVAL not set to valid int or string containing int! Setting to default = 60.')
    sleep_interval = 60

if turn_off is True:
    import sys

    sys.exit(-1)

if tag:
    logging.debug("Tag: %s" % tag)
else:
    logging.debug("Branch: %s" % branch)
logging.debug("Sync root: %s" % sync_root)
logging.debug("Target sesam instance: %s" % sesam_api)

if tag:
    logging.debug("Since the environmental variable 'TAG' is set, the variable 'BRANCH' is ignored")

upload_variables = var_file_path is not None
upload_secrets = vault_git_token is not None and vault_mounting_point is not None and vault_url is not None

clone_with_git_token = False  # Cloning with git token instead of deploy token if username is set.
if git_username is not None:
    clone_with_git_token = True
    logging.info('Cloning with username and access token!')
    if os.environ.get('LOG_LEVEL', 'INFO') == 'DEBUG':
        logging.warning('DEPLOY_TOKEN (git token) will be exposed in the logs because log_level is set to DEBUG!')
else:
    logging.info('Cloning with deploy token!')


## remove a directory if it exists
def remove_if_exists(path):
    if os.path.exists(path):
        for root, dirs, files in os.walk(path):
            # os.remove(path)
            shutil.rmtree(path)


## clone a github repo version2: using python libraries
def clone_git_repov2():
    ssh_cmd = 'ssh -o "StrictHostKeyChecking=no" -i id_deployment_key'
    remove_if_exists(git_cloned_dir)
    logging.info('cloning %s', git_repo)

    branch_or_tag = branch
    if tag:
        branch_or_tag = tag
    if clone_with_git_token is False:
        Repo.clone_from(git_repo, git_cloned_dir, env=dict(GIT_SSH_COMMAND=ssh_cmd), branch=branch_or_tag)
    else:  # If you are using personal access token instead of deploy token, you also need username.
        git_url = None
        if git_repo.startswith('https://'):
            git_url = git_repo.replace('https://', '')
        elif git_repo.startswith('git@'):
            git_url = git_repo.replace('git@', '').replace(':', '/')
        elif git_repo.startswith('github.com:'):
            git_url = git_repo.replace(':', '/')
        else:
            git_url = git_repo
        url = f'https://{git_username}:{deploy_token}@{git_url}'
        Repo.clone_from(url, git_cloned_dir, progress=to_progress_instance(None), branch=branch_or_tag)


## remove .git, .gitignore and README from a cloned github repo directory
def clean_git_repo():
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
    variables = None
    secrets = None
    if upload_variables:
        variables_in_conf = regex_findall(r'\$ENV\((\S*?)\)', node_string)  # Find env vars
        variables: dict = load_json(open(git_cloned_dir + var_file_path).read())
        for var in variables_in_conf:  # Verify they exist in git repo
            if var not in variables:
                logging.error(f'Missing env var {var} in variables file {var_file_path}')
    if upload_secrets:
        secrets_in_conf = regex_findall(r'\$SECRET\((\S*?)\)', node_string)  # Find secrets
        vault = Vaulter(vault_url, vault_git_token, vault_mounting_point,
                        vault_path_prefix=vault_path_prefix)  # Create keyvault object
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


def compare_json_dict_list(old, new):

    #Filter to remove autodeployer changes from comparison
    filtered1 = list(filter(lambda x: x['_id'] != autodeployer_config_path.split('/')[-1].split('.conf.json')[0], old))
    sorted1 = sorted(filtered1, key=lambda i: i['_id'])

    filtered2 = list(filter(lambda x: x['_id'] != autodeployer_config_path.split('/')[-1].split('.conf.json')[0], new))
    sorted2 = sorted(filtered2, key=lambda i: i['_id'])

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
                           headers={'Accept': 'application/zip', 'Authorization': 'bearer ' + jwt}, verify=verify_ssl)
    if request.status_code == 200:
        logging.info("OK, the Sesam api answered with status code: %s" % request.status_code)
        with open(sesam_checkout_dir + "/" + "sesam.zip", 'wb') as f:
            for chunk in request.iter_content(1024):
                f.write(chunk)
    else:
        logging.error("Non 200 status code from the Sesam api, got: %s" % request.status_code)


## upload the sesam configuration straight from the cloned git repo
def upload_payload():
    request = requests.put(url=sesam_api + "/config?force=true",
                           data=open(zipped_payload, 'rb').read(),
                           headers={'Content-Type': 'application/zip', 'Authorization': 'bearer ' + jwt},
                           verify=verify_ssl)
    if request.status_code == 200:
        logging.info("OK. The Sesam api answered with status code: %s" % request.status_code)
    else:
        logging.error(f"Non 200 status code from the Sesam api, got: '{request.status_code}'. Content: '{request.content}'")


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

def check_and_replace_orchestrator_pipes():
    for old_filename in os.listdir(sesam_checkout_dir + "/unpacked/pipes/"):
        with open(os.path.join(sesam_checkout_dir + "/unpacked/pipes/", old_filename), 'r') as f: # open in readonly mode
            old_file = load_json(f.read())
            try:
                old_file["metadata"]["orchestrator"]["original_configuration"]
                for new_filename in os.listdir(git_cloned_dir + "/sesam-node/pipes/"):
                    with open(os.path.join(git_cloned_dir + "/sesam-node/pipes/", new_filename), 'r') as g: # open in readonly mode
                        new_file = load_json(g.read())
                        if old_file["metadata"]["orchestrator"]["original_configuration"] == new_file:
                            logging.info("The pipe %s is restored to orchestrator mode" % new_file["_id"])
                            with open(os.path.join(payload_dir + "/pipes/", new_filename), 'w') as h:
                                h.write(dump_json(old_file))
            except KeyError:
                None
def check_and_replace_orchestrator_systems():
    for old_filename in os.listdir(sesam_checkout_dir + "/unpacked/systems/"):
        with open(os.path.join(sesam_checkout_dir + "/unpacked/systems/", old_filename), 'r') as f: # open in readonly mode
            old_file = load_json(f.read())
            try:
                old_file["metadata"]["orchestrator"]["original_configuration"]
                for new_filename in os.listdir(git_cloned_dir + "/sesam-node/systems/"):
                    with open(os.path.join(git_cloned_dir + "/sesam-node/systems/", new_filename), 'r') as g: # open in readonly mode
                        new_file = load_json(g.read())
                        if old_file["metadata"]["orchestrator"]["original_configuration"] == new_file:
                            logging.info("The system %s is restored to orchestrator mode" % new_file["_id"])
                            with open(os.path.join(payload_dir + "/systems/", new_filename), 'w') as h:
                                h.write(dump_json(old_file))
            except KeyError:
                None

if __name__ == '__main__':
    while True:
        os.chdir("/service")
        if clone_with_git_token is False:
            with open("id_deployment_key", "w") as key_file:
                key_file.write(os.environ['DEPLOY_TOKEN'])
            os.chmod("id_deployment_key", 0o600)

        ## we first clone the repo, clean it up, and extract the relevant files to prepare the payload.
        clone_git_repov2()
        clean_git_repo()
        prepare_payload()
        ## we then download the sesam configuration from the api, unpack it, check it ...
        download_sesam_zip()
        unpack_sesam_zip()
        check_for_unknown()
        copy_autodeployer()

        new_node = load_sesam_files_as_json(git_cloned_dir + "/" + sync_root)
        old_node = load_sesam_files_as_json(sesam_checkout_dir + "/" + "unpacked")
        if not compare_json_dict_list(old_node, new_node):
            # Verify variables & secrets if specified
            if upload_variables or upload_secrets:
                variables, secrets = verify_node(new_node)
                # Upload variables & secrets
                session = requests.session()
                session.verify = verify_ssl
                session.headers = {'Authorization': f'bearer {jwt}'}
                if upload_secrets and secrets is not None:
                    if do_put(session, f'{sesam_api}/secrets', json=secrets) != 0:
                        logging.error('Failed to upload secrets to node!')
                elif upload_secrets and secrets is None:
                    logging.error('Upload secrets is true but could not get secrets to upload!')
                if upload_variables and variables is not None:
                    if do_put(session, f'{sesam_api}/env', json=variables) != 0:
                        logging.error('Failed to upload variables to node!')
                elif upload_variables and variables is None:
                    logging.error('Upload variables is true but could not get variables to upload!')
            if orchestrator:
                check_and_replace_orchestrator_pipes()
                check_and_replace_orchestrator_systems()
            logging.info(f"Uploading new configuration from github to node {sesam_api}")
            zip_payload()
            upload_payload()
        else:
            logging.info("No change, doing nothing.")
        logging.info(f"Going to sleep for {sleep_interval} seconds!")
        sleep(sleep_interval)
