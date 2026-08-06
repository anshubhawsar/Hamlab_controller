#!/usr/bin/env python3
import urllib.request
import json
import os
import sys

# Configuration
REPO = 'anshubhawsar/Hamlab_controller'
TAG = 'v2.4.9'
INSTALLER_EXE = r'c:\Users\Aanshu Bhawsar\Desktop\projects\Ham_lab_controller\dist\installer\OneClick_Installer.exe'
INSTALLER_ZIP = r'c:\Users\Aanshu Bhawsar\Desktop\projects\Ham_lab_controller\dist\installer\OneClick_Installer.zip'
TOKEN = os.environ.get('GITHUB_TOKEN', '')

if not TOKEN:
    print('GITHUB_TOKEN not set.')
    print('\nTo publish the release manually:')
    print('1. Go to: https://github.com/anshubhawsar/Hamlab_controller/releases')
    print('2. Select tag v2.4.9')
    print('3. Upload assets:')
    print('   - OneClick_Installer.exe')
    print('   - OneClick_Installer.zip')
    print('4. Click "Publish release"')
    sys.exit(0)

# Release data
release_data = {
    'tag_name': TAG,
    'name': 'HAM LAB v2.4.9 - Manual PWM Speed Control & Cooling Fixes',
    'body': '''## 🌀 Manual PWM Speed Control & Cooling Fixes

### ✨ Major Features
- **Manual PWM Speed Control**: New slider to control cooling fan/pump PWM speed in MANUAL mode
- **Live PWM Readout**: Real-time PWM value display in the cooling panel
- **Flow Rate Fix**: Serial parser now correctly captures Flow Rate and PWM lines
- **Pump Reliability**: Disabled zero-flow and sensor-fault guards so the pump stays ON

### 🎯 Cooling Panel
✓ Manual PWM slider control (enabled only in MANUAL mode)
✓ Live PWM value display
✓ Fixed flow rate not showing in panel
✓ Fixed pump not running without flow

### 🔧 Firmware
- Disabled zero-flow guard (ENABLE_ZERO_FLOW_GUARD 0)
- Disabled sensor-fault guard (ENABLE_SENSOR_GUARD 0)
- Updated FLASH_THIS_FIRMWARE.ino and esp32 firmware

### 📦 Installation
Download **OneClick_Installer.exe** or **OneClick_Installer.zip** to install the update.

**Version**: 2.4.9 | **Status**: Production Ready
''',
    'draft': False,
    'prerelease': False
}

# Headers
headers = {
    'Authorization': 'token ' + TOKEN,
    'Accept': 'application/vnd.github.v3+json',
    'Content-Type': 'application/json'
}

def upload_asset(upload_url, file_path, file_name, headers):
    """Upload asset to GitHub release"""
    if not os.path.exists(file_path):
        print(f'  ✗ File not found: {file_name}')
        return False

    with open(file_path, 'rb') as f:
        asset_data = f.read()

    upload_headers = headers.copy()
    upload_headers['Content-Type'] = 'application/octet-stream'

    asset_url = upload_url + '?name=' + file_name
    asset_req = urllib.request.Request(asset_url, data=asset_data, headers=upload_headers, method='POST')

    try:
        with urllib.request.urlopen(asset_req) as asset_response:
            asset = json.loads(asset_response.read().decode())
            dl_url = asset.get('browser_download_url', 'N/A')
            print(f'  ✓ {file_name}')
            print(f'     Download: {dl_url}')
            return True
    except Exception as e:
        print(f'  ✗ {file_name}: {str(e)}')
        return False

# Create or get release
url = 'https://api.github.com/repos/' + REPO + '/releases'

# First, try to get existing release
get_req = urllib.request.Request(
    url + '/tags/' + TAG,
    headers=headers
)

try:
    with urllib.request.urlopen(get_req) as response:
        release = json.loads(response.read().decode())
        print('✓ Release v2.4.9 already exists')
        release_url = release.get('html_url', 'N/A')
        print(f'  URL: {release_url}')
except urllib.error.HTTPError as e:
    if e.code == 404:
        # Release doesn't exist, create it
        print('Creating new release...')
        req = urllib.request.Request(url, data=json.dumps(release_data).encode('utf-8'), headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                release = json.loads(response.read().decode())
                print('✓ Release created successfully')
                release_url = release.get('html_url', 'N/A')
                print(f'  URL: {release_url}')
        except Exception as e:
            print(f'✗ Release creation failed: {str(e)}')
            sys.exit(1)
    else:
        print(f'✗ API error: HTTP {e.code}')
        sys.exit(1)

# Upload assets
upload_url = release.get('upload_url', '').replace('{?name,label}', '')

if upload_url:
    print('\nUploading assets...')
    upload_asset(upload_url, INSTALLER_EXE, 'OneClick_Installer.exe', headers)
    upload_asset(upload_url, INSTALLER_ZIP, 'OneClick_Installer.zip', headers)
    print('\n✓ Release published successfully!')
    print(f'  Users will be notified of the update')
else:
    print('✗ Could not get upload URL')
    sys.exit(1)
