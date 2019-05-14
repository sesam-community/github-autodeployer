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
import json

__author__ = "Enrico Razzetti"

sesam_api = os.environ.get('SESAM_API_URL', 'http://sesam-node:9042/api')  # ex: "https://abcd1234.sesam.cloud/api"
jwt = os.environ.get('JWT')
git_repo = os.environ.get('GIT_REPO')  # the project you want to sync from
branch = os.environ.get('BRANCH', 'master')  # the branch of the project you want to use for a sync
sync_root = os.environ.get('SYNC_ROOT', '/')  # the top directory from the github repo you want to use for sync
deploy_token = os.environ.get('DEPLOY_TOKEN')  # ssh deploy key for this particular project
autodeployer_config_path = os.environ.get('AUTODEPLOYER_PATH')  # path to system config in current node config
env_vars_filename = os.getenv('ENV_VARS_FILENAME', False)  # Name of git environment variables file if you want to sync

# internal, skeleton, don't touch, you perv! *touchy, touchy*

git_cloned_dir = "/tmp/git_upstream_clone"
sesam_checkout_dir = "/tmp/sesam_conf"
zipped_payload = "/tmp/payload/sesam.zip"
payload_dir = "/tmp/payload"
env_var_git_dir = "/tmp/env_var_git_file"
env_var_sesam_dir = "/tmp/env_var_sesam_file"
isvalidfile = False

# set logging
log_level = logging.getLevelName(os.environ.get('LOG_LEVEL', 'INFO'))  # default log level = INFO
logging.basicConfig(level=log_level)  # dump log to stdout

logging.info(datetime.datetime.now())
logging.debug("Git repo: %s" % git_repo)
logging.debug("Branch: %s" % branch)
logging.debug("Sync root: %s" % sync_root)
logging.debug("Target sesam instance: %s" % sesam_api)


# remove a directory if it exists
def remove_if_exists(path):
    if os.path.exists(path):
        shutil.rmtree(path)


# clone a github repo version2: using python libraries
def clone_git_repov2():
    ssh_cmd = 'ssh -o "StrictHostKeyChecking=no" -i id_deployment_key'
    remove_if_exists(git_cloned_dir)
    logging.info('cloning %s', git_repo)
    Repo.clone_from(git_repo, git_cloned_dir, env=dict(GIT_SSH_COMMAND=ssh_cmd), branch=branch)


# remove .git, .gitignore and README from a cloned github repo directory
def clean_git_repo():
    # os.chdir(git_cloned_dir)
    for path in glob.glob(git_cloned_dir + "/" + '.git'):
        shutil.rmtree(path)
    for path in glob.glob(git_cloned_dir + "/" + '.gitignore'):
        os.remove(path)
    for path in glob.glob(git_cloned_dir + "/" + 'README.md'):
        os.remove(path)


# zip a directory
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


# create a directory
def create_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)


# match the sesam configuration files and copy them to the payload directory
def extract_sesam_files_from(dirpath):
    for name in os.listdir(dirpath):
        path = os.path.join(dirpath, name)
        if os.path.isfile(path):
            if fnmatch.fnmatch(name, 'node-metadata.conf.json'):
                shutil.copyfile(path, payload_dir + "/" + name)
            if env_vars_filename:
                if fnmatch.fnmatch(name, env_vars_filename):
                    shutil.copyfile(path, env_var_git_dir + "/" + name)
                    format_jsonfile(env_var_git_dir + "/" + name)
        else:
            extract_sesam_files_from(path)
            if os.path.isdir(path):
                if fnmatch.fnmatch(name, 'pipes'):
                    shutil.copytree(path, payload_dir + "/" + name)
                elif fnmatch.fnmatch(name, 'systems'):
                    shutil.copytree(path, payload_dir + "/" + name)


# Need sort and format git file as per sesam node key-value pair
def format_jsonfile(filepath):
    jsondict = get_env_variables_fromfile(filepath)
    f = open(filepath, "w+")
    f.write(json.dumps(jsondict, sort_keys=True, indent=2, separators=(',', ': ')))
    f.close()


def prepare_payload():
    remove_if_exists(payload_dir)
    remove_if_exists(env_var_git_dir)
    create_dir(env_var_git_dir)
    create_dir(payload_dir)
    extract_sesam_files_from(git_cloned_dir + sync_root)


# download the sesam configuration
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


def download_sesam_env_variables():
    remove_if_exists(env_var_sesam_dir)
    create_dir(env_var_sesam_dir)
    response = requests.get(url=sesam_api + "/env",
                            headers={'Accept': 'application/json', 'Authorization': 'bearer ' + jwt})
    if response.status_code == 200:
        logging.info(
            "OK, the Sesam api to download sesam_env_variables, answered with status code: %s" % response.status_code)
        f = open(env_var_sesam_dir + "/" + env_vars_filename, "w+")
        f.write(json.dumps(response.json(), sort_keys=True, indent=2, separators=(',', ': ')))
        f.close()
    else:
        logging.error("Non 200 status code from the Sesam api, got: %s" % response.status_code)


# Writing new or updated environment variables list on Node instance.
def post_env_variables_list():
    try:
        if isvalidfile:
            env_variables = get_env_variables_fromfile(env_var_git_dir + "/" + env_vars_filename)
            resp = requests.put(url=sesam_api + "/env", json=env_variables,
                                headers={'Accept': 'application/json', 'Authorization': 'bearer ' + jwt})
            if resp.status_code == 200:
                log_to_node_screen()
            else:
                logging.error(
                    "Failed to post variables node. Endpoint returned status code {}".format(resp.status_code))
    except Exception:
        logging.error("Failed to post variables on node. Error occured.")


def log_to_node_screen():
    all_node_vars = get_env_variables_fromfile(env_var_sesam_dir + "/" + env_vars_filename)
    if len(all_node_vars) > 0:
        logging.info(
            "The following node environment variables has been updated with values from your git env-configuration file : ['{}']".format(
                env_vars_filename))
        logging.info(json.dumps(all_node_vars, indent=2, sort_keys=True))
        logging.info("You can put above old node-values back in your git env-configuration file if you want it back.")


def get_env_variables_fromfile(filepath):
    try:
        if os.path.isfile(filepath):
            with open(filepath, 'r') as f:
                env_vars = json.load(f)
                global isvalidfile
                isvalidfile = True  # setting true ony if git git environment variables file is valid
                return env_vars
        else:
            logging.error("Environment variables file not found on path {}".format(filepath))
    except Exception:
        logging.error("Environment variables file : ['{}'] is not in correct format.".format(env_vars_filename))


# upload the sesam configuration straight from the cloned git repo
def upload_payload():
    logging.debug('hvor er jeg?' + os.getcwd())
    request = requests.put(url=sesam_api + "/config?force=true",
                           data=open(zipped_payload, 'rb').read(),
                           headers={'Content-Type': 'application/zip', 'Authorization': 'bearer ' + jwt})
    if request.status_code == 200:
        logging.info("OK. The Sesam api answered with status code: %s" % request.status_code)
    else:
        logging.error("Non 200 status code from the Sesam api, got: %s" % request.status_code)


# unzip the downloaded sesam zip archive
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


# check that there is no error in the downloaded zip.
# we observed that if the downloaded archive from archive
# contains a directory called "unknown" probably a json file
# with data that does not belong to, say pipes, has been added there.
# nice to raise a flag then.


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


# compare the content of two directories and the content of the files
def compare_env_directories(dir1, dir2):
    dirs_cmp = filecmp.dircmp(dir1, dir2)
    if len(dirs_cmp.left_only) > 0 or len(dirs_cmp.right_only) > 0 or \
            len(dirs_cmp.funny_files) > 0:
        logging.info("These are new files from Github : %s" % dirs_cmp.right_only)
        logging.info("These files will be gone from Sesam : %s" % dirs_cmp.left_only)
        return False
    (_, mismatch, errors) = filecmp.cmpfiles(
        dir1, dir2, dirs_cmp.common_files, shallow=False)
    if len(mismatch) > 0 or len(errors) > 0:
        logging.info("Environment variables not in sync with git env-config: %s" % dirs_cmp.diff_files)
        return False
    for common_dir in dirs_cmp.common_dirs:
        new_dir1 = os.path.join(dir1, common_dir)
        new_dir2 = os.path.join(dir2, common_dir)
        if not compare_env_directories(new_dir1, new_dir2):
            return False
    return True


def compare_config_directories(dir1, dir2):
    dirs_cmp = filecmp.dircmp(dir1, dir2)
    if len(dirs_cmp.left_only) > 0 or len(dirs_cmp.right_only) > 0 or \
            len(dirs_cmp.funny_files) > 0:
        logging.info("These are new files from Github : %s" % dirs_cmp.right_only)
        logging.info("These files will be gone from Sesam : %s" % dirs_cmp.left_only)
        return False
    (_, mismatch, errors) = filecmp.cmpfiles(
        dir1, dir2, dirs_cmp.common_files, shallow=False)
    if len(mismatch) > 0 or len(errors) > 0:
        logging.info("These files changed : %s" % dirs_cmp.diff_files)
        return False
    for common_dir in dirs_cmp.common_dirs:
        new_dir1 = os.path.join(dir1, common_dir)
        new_dir2 = os.path.join(dir2, common_dir)
        if not compare_config_directories(new_dir1, new_dir2):
            return False
    return True


def process_environment_variables():
    if env_vars_filename and isvalidfile:
        download_sesam_env_variables()
        # ... then we compare the two directories, and if there are differences, we post the github's environment variables
        #  back to the sesam node. This overwrites the existing configuration.
        if not compare_env_directories(env_var_sesam_dir, env_var_git_dir):
            post_env_variables_list()
        else:
            logging.info("No change in environment variables, doing nothing.")
    else:
        if not env_vars_filename:
            logging.warning(
                "Skipping environment variable checking, since no value provided to ENV_VARS_FILENAME in system config.")
        if not isvalidfile:
            logging.error(
                "Skipping environment variable updation, since provided git environment variable file is not in correct format.")


if __name__ == '__main__':
    os.chdir("/service")
    with open("id_deployment_key", "w") as key_file:
        key_file.write(os.environ['DEPLOY_TOKEN'])
    os.chmod("id_deployment_key", 0o600)

    # we first clone the repo, clean it up, and extract the relevant files to prepare the payload.
    clone_git_repov2()
    clean_git_repo()
    prepare_payload()
    # we now download the sesam node environment variables from the api and process it ...
    process_environment_variables()
    # we then download the sesam configuration from the api, unpack it, check it ...
    download_sesam_zip()
    unpack_sesam_zip()
    check_for_unknown()
    copy_autodeployer()
    # ... then we compare the two directories, and if there are differences, we pack the payload
    # and push it back to the api. This overwrites the existing configuration.
    if not compare_config_directories(sesam_checkout_dir + "/" + "unpacked", payload_dir):
        logging.info("Uploading new configuration from github to your Sesam node api.")
        zip_payload()
        upload_payload()
    else:
        logging.info("No change in node configuration, doing nothing.")
