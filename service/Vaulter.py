from hvac import Client
from hvac.exceptions import InvalidPath

from sys import exit


class Vaulter:
    def __init__(self, url,  git_token, mount_point):
        self.client = Client(url=url)
        self.mount_point = mount_point
        self.client.auth.github.login(git_token)
        self.missing_secrets = []
        if not self.client.is_authenticated():
            print(f'Cannot authenticate vault {url}. Exiting.')
            exit(-1)

    def get_secret(self, secret):
        return_value = None
        try:
            response = self.client.secrets.kv.v2.read_secret_version(mount_point=self.mount_point, path=secret)
            key_value = response['data']['data']
            for k in key_value:
                return_value = key_value[k]
                break
        except InvalidPath as e:
            print(f'Could not find {secret} in vault. Invalid path: "{e}"')
            self.missing_secrets.append(secret)
        return return_value

    def get_secrets(self, secrets):
        output = {}
        for s in secrets:
            output[s] = self.get_secret(s)
        return output

    def verify(self):
        if len(self.missing_secrets) != 0:
            return False
        return True

    def get_missing_secrets(self):
        return self.missing_secrets
