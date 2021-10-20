import requests

import settings_secret
import settings_secret

# setting
line_token = str(settings_secret.token)

print(line_token)


def line_notify(text):
    url = "https://notify-api.line.me/api/notify"
    data = {"message": text}
    headers = {"Authorization": "Bearer " + line_token}
    requests.post(url, data=data, headers=headers)
