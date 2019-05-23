## Github Autodeployer

A microservice to sync a sesam instance configuration with a github repository containing the pipes, systems and nodes-metadata.conf.json

### User Case

Given a Sesam node with an API, and a GitHub repository containing, in a defined branch, in one defined subdirectory, the configuration for the systems and pipes (and "node-metadata.conf.json"), we want to automatically sync
the node with the configuration coming from GitHub. The microservice will regularly (through a cron job) compare the configuration of the sesam node with the configuration present on GitHub.
If the two are different, the microservice will pull the GitHub configuration and overwrite the Sesam configuration with it.

Warning, use with care. Notes:

* The microservice always assumes that the GitHub configuration is authoritative. No matter what.
* The files are compared using the filecmp python3 library. Example [here.](https://stackoverflow.com/a/6681395)
* The microservice always automatically _overwrites_ the local Sesam node configuration.
* The microservice does not have to be a part of the GitHub configuration because it add itself to the configuration during its run. 

## Environment variables

`SESAM_API_URL` - In the format "https://address/api". You don't need to add this variable, since per default the microservice uses the internal docker container address. That is the safest and recommended choice. In any case, you can specify the url of the api of the instance you want to control here (most probably, the address of the Sesam instance you are adding the microservice to). Make sure you don't end up controlling the "wrong" Sesam instance here, since the address will be probably resolved correctly and if there is no firewall blocking the traffic, the configuration of that Sesam node will be overwritten :-)

`JWT` - JSON Web Token granting access to the instance. This should be added as a secret to the datahub / variables section in your Settings.

`DEPLOY_TOKEN` - The private deploy key. It has to be allowed to clone the repo. This has to be created by you (follow the guide on GitHub https://developer.github.com/v3/guides/managing-deploy-keys/), under your repo Settings > Deploy Keys. The  public part of this key get pasted to git and the private one should be added to secrets in your sesam node.

`GIT_REPO` - The name of the repository containing the configuration to sync.

`AUTODEPLOYER_PATH` - The path where the system should add itself, so within systems-folder. Example: systems/github-autodeployer.conf.json

`BRANCH` - The branch of the repo to use. If not specified, defaults to "master".

`SYNC_ROOT` - Defaults to the top directory, or "/". The path of the top directory in your GitHub repo to use for sync. Might be a subdirectory of the repo, for example if you have multiple configurations in different directories of the same repository.

`ENV_VARS_FILENAME`-  it should be name of your  environment variables file (like "test-env.json" or any name that you like) on your git-repo and in Sesam system config.Name must be same on both places.By default, it will not perform any synchronization if this variable is not persent in sesam system config or file-names are not same on both places.

## Example Sesam System Config
This configuration assumes that you have defined both a "github_token" and a "jwt" secret under settings > datahub > secrets, containing the relative correct strings. Make sure also that the GitHub token you are using belongs to a user
with access permission to the private GitHub repository you are using. Some variables have been omitted, and using defaults: we assume we are using the master branch, and "pipes", "systems" and node-metadata.conf.json are in the top directory of that repository.

```
{
  "_id": "watcher",
  "type": "system:microservice",
  "docker": {
    "environment": {
      "GIT_REPO": "$ENV(git_repo)",
      "DEPLOY_TOKEN": "$SECRET(deploy-token)",
      "AUTODEPLOYER_PATH": "systems/github-autodeployer.conf.json",
      "JWT": "$SECRET(jwt)",
    },
    "image": "sesamcommunity/github-autodeployer:latest",
    "port": 5000
  }
}
```

## Example Sesam System config specifying all available environment variables
Same as above, just showing all the available variables.

```
{
  "_id": "watcher",
  "type": "system:microservice",
  "docker": {
    "environment": {
      "BRANCH": "master",
      "AUTODEPLOYER_PATH": "systems/github-autodeployer.conf.json"
      "GIT_REPO": "$ENV(git_repo)",
      "JWT": "$SECRET(jwt)",
      "SESAM_API_URL": "https://b893jus.sesam.cloud/api",
      "SYNC_ROOT": "sesam-home/sesam-node",
      "ENV_VARS_FILENAME": "test-env.json"
    },
    "image": "sesamcommunity/github-autodeployer:latest",
    "port": 5000
  }
}
```


## Example tutorial

1. Make sure you have all you need. A Deploy key for a repository allowed to clone the repo. A jwt string for your Sesam node.
2. Add the secrets to the Sesam node under settings > datahub > secrets. Check that the names match with your configuration.
3. Make sure you have your GitHub repo in place, and you have your configuration stored under the SYNC_ROOT defined directory. Pipes, systems and
node-metdata.conf.json will be sync'ed.
4. On your node, add a new system, cut and paste the microservice json into the text field. Make sure the two json are identical. Press "save".
Press "Refresh" and wait for the logs to appear.
5. Every minute the microservice will inform you of the changes from GitHub applied (if any) and the result.

## TODO

At the moment is not possible to pass the microservice an enviroment variable that controls how often we poll GitHub for changes.
One of the reasons for this is that we use cron, and cron has a relatively speaking bizarre definition of time intervals. While it might
be very easy to implement an environment variable that defines how many minutes ( at the moment the interval of one minute is hardcoded in the Dockerfile )
should cron wait before running the script again, going under the minute interval would not be possible ( unless doing kinky stuff ).
Another option would be to implement a sleep loop with a variable in seconds in the python script. But that is not optimal either, since if, for any reason,
the process dies, so will the container, and Sesam does not have a mechanism to restart a service. The advantage of running cron in the foreground lays in its
simplicity and robustness. This is not a crucial feature to implement, since no action will be performed unless there is a change on the upstream GitHub
repository anyway. How often the repo is checked for change is not so critical, as long as we choose wisely the branch we use to control the microservice.
Further on, how desirable is the scenario for which an operator can automate a deployment of a change to a Sesam node in production, without her being available
in the case of needing to rollback? Enough with excuses, this could be implemented.
