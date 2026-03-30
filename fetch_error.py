import requests
url = 'http://127.0.0.1:8000/api/v1/sensors/latest/'
try:
    r = requests.get(url)
    print(f'STATUS: {r.status_code}')
    print('BODY:')
    print(r.text)
except Exception as e:
    print(f'ERROR: {e}')
