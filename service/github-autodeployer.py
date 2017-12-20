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

__author__ = "Enrico Razzetti"

sesam_api = os.environ.get('SESAM_API_URL', 'http://sesam-node:9042/api') # ex: "https://abcd1234.sesam.cloud/api"
jwt = os.environ.get('JWT')
token = os.environ.get('GITHUB_TOKEN')
username = os.environ.get('GITHUB_USER') # not the github user, but the private user owning the github repo you want to sync.
private_project = os.environ.get('GITHUB_PROJECT')
branch = os.environ.get('BRANCH', 'master') # the branch of the project you want to use for a sync
sync_root = os.environ.get('SYNC_ROOT', '/') # the top directory from the github repo you want to use for sync

## internal, skeleton, don't touch, you perv!

repo_url = "https://" + token + ":x-oauth-basic@github.com/" + username + "/" + private_project + ".git"
git_cloned_dir = "/tmp/git_upstream_clone"
sesam_checkout_dir = "/tmp/sesam_conf/"
zipped_sesam_archive = "/tmp/sesam_conf/sesam.zip"
zipped_payload = "/tmp/sesam.zip"
payload_dir = "/tmp/payload"


# set logging
log_level = logging.getLevelName(os.environ.get('LOG_LEVEL', 'INFO'))  # default log level = INFO
logging.basicConfig(level=log_level)  # dump log to stdout

logging.info(datetime.datetime.now())
logging.debug("Github organization  : %s" % username)
logging.debug("Github repo: %s" % private_project)
logging.debug("Branch: %s" % branch)
logging.debug("Sync root: %s" % sync_root)
logging.debug("Target sesam instance: %s" % sesam_api)


## remove a directory if it exists
def remove_if_exists(path):
    if os.path.exists(path):
        for root, dirs, files in os.walk(path):
            # os.remove(path)
            shutil.rmtree(path)

## clone a github repo version2: using python libraries
def clone_git_repov2():
    remove_if_exists(git_cloned_dir)
    cloned_repo = Repo.clone_from(repo_url, git_cloned_dir, branch=branch)


## remove .git, .gitignore and README from a cloned github repo directory
def clean_git_repo():
    os.chdir(git_cloned_dir)
    for path in glob.glob('.git'):
        shutil.rmtree(path)
    for path in glob.glob('.gitignore'):
        os.remove(path)
    for path in glob.glob('README.md'):
        os.remove(path)


## zip a directory
def zip_payload():
    remove_if_exists(zipped_payload)
    with zipfile.ZipFile(zipped_payload, 'w', zipfile.ZIP_DEFLATED) as zippit:
        os.chdir(payload_dir)
        for file in glob.glob('**', recursive=True):
            if os.path.isfile(file):
                zippit.write(file)

## create a directory
def create_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)


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
        with open(sesam_checkout_dir + "sesam.zip", 'wb') as f:
            for chunk in request.iter_content(1024):
                f.write(chunk)
    else:
        logging.error("Non 200 status code from the Sesam api, got: %s" % request.status_code)


## upload the sesam configuration straight from the cloned git repo
def upload_payload():
    request = requests.put(url=sesam_api + "/config?force=true",
                           data=open(zipped_payload, 'rb').read(),
                           headers={'Content-Type': 'application/zip', 'Authorization': 'bearer ' + jwt})
    if request.status_code == 200:
        logging.info("OK. The Sesam api answered with status code: %s" % request.status_code)
    else:
        logging.error("Non 200 status code from the Sesam api, got: %s" % request.status_code)


## unzip the downloaded sesam zip archive
def unpack_sesam_zip():
    remove_if_exists(sesam_checkout_dir + "unpacked")
    create_dir(sesam_checkout_dir + "unpacked")
    zip_ref = zipfile.ZipFile(sesam_checkout_dir + "sesam.zip", 'r')
    zip_ref.extractall(sesam_checkout_dir + "unpacked")
    zip_ref.close()


## check that there is no error in the downloaded zip.
## we observed that if the downloaded archive from archive
## contains a directory called "unknown" probably a json file
## with data that does not belong to, say pipes, has been added there.
## nice to raise a flag then.

def check_for_unknown():
    if os.path.exists(sesam_checkout_dir + "/unpacked" + "/unknown"):
        logging.warning("\n")
        logging.warning("WARNING:")
        logging.warning("Looks like Sesam has flagged some of your github committed data as gibberish:")
        logging.warning("I detected a directory called 'unknown' in the dowloaded configuration from the node.")
        logging.warning("This could be, for example, some data file added to the pipes directory. But i don't know for sure.")
        logging.warning("This error is in your github committed code and should be corrected before continuing with your workflow,")
        logging.warning("else, prepare for unexpected behaviour. Hic Sunt Leones. You have been warned.")
        logging.warning("\n")


## compare the content of two directories and the content of the files
def compare_directories(dir1, dir2):
    dirs_cmp = filecmp.dircmp(dir1, dir2)
    if len(dirs_cmp.left_only) > 0 or len(dirs_cmp.right_only) > 0 or \
            len(dirs_cmp.funny_files) > 0:
        logging.info("These are new files from Github : %s" % dirs_cmp.right_only )
        logging.info("These files will be gone from Sesam : %s" % dirs_cmp.left_only )
        return False
    (_, mismatch, errors) = filecmp.cmpfiles(
        dir1, dir2, dirs_cmp.common_files, shallow=False)
    if len(mismatch) > 0 or len(errors) > 0:
        logging.info("These files changed : %s" % dirs_cmp.diff_files )
        return False
    for common_dir in dirs_cmp.common_dirs:
        new_dir1 = os.path.join(dir1, common_dir)
        new_dir2 = os.path.join(dir2, common_dir)
        if not compare_directories(new_dir1, new_dir2):
            return False
    return True


if __name__ == '__main__':
    ## we first clone the repo, clean it up, and extract the relevant files to prepare the payload.
    clone_git_repov2()
    clean_git_repo()
    prepare_payload()
    ## we then download the sesam configuration from the api, unpack it, check it ...
    download_sesam_zip()
    unpack_sesam_zip()
    check_for_unknown()
    ## ... then we compare the two directories, and if there are differences, we pack the payload
    ## and push it back to the api. This overwrites the existing configuration.
    if not compare_directories(sesam_checkout_dir + "unpacked", payload_dir):
        logging.info("Uploading new configuration from github to your Sesam node api.")
        zip_payload()
        upload_payload()
    else:
        logging.info("No change, doing nothing.")
