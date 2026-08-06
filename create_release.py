#!/usr/bin/env python3
import urllib.request
import json
import os

# Configuration
REPO = 'anshubhawsar/Hamlab_controller'
TAG = 'v2.4.9'
INSTALLER_PATH = r'c:\Users\Aanshu Bhawsar\Desktop\projects\Ham_lab_controller\dist\installer\OneClick_Installer.exe'
TOKEN = os.environ.get('GITHUB_TOKEN', '')

if not TOKEN:
    print('GITHUB_TOKEN not set. Automatic release creation skipped.')
    print('\nTo create the release manually, visit:')
    print('https://github.com/anshubhawsar/Hamlab_controller/releases/tag/v2.4.9')
    exit(0)

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

# Create release
url = 'https://api.github.com/repos/' + REPO + '/releases'
req = urllib.request.Request(url, data=json.dumps(release_data).encode('utf-8'), headers=headers)

try:
    with urllib.request.urlopen(req) as response:
        release = json.loads(response.read().decode())
        release_url = release.get('html_url', 'N/A')
        print('✓ Release created successfully')
        print('  URL: ' + release_url)

        # Upload installer
        upload_url = release.get('upload_url', '').replace('{?name,label}', '')

        if os.path.exists(INSTALLER_PATH) and upload_url:
            with open(INSTALLER_PATH, 'rb') as f:
                installer_data = f.read()

            upload_headers = headers.copy()
            upload_headers['Content-Type'] = 'application/octet-stream'

            asset_url = upload_url + '?name=OneClick_Installer.exe'
            asset_req = urllib.request.Request(asset_url, data=installer_data, headers=upload_headers, method='POST')

            try:
                with urllib.request.urlopen(asset_req) as asset_response:
                    asset = json.loads(asset_response.read().decode())
                    dl_url = asset.get('browser_download_url', 'N/A')
                    print('✓ Installer uploaded successfully')
                    print('  Download: ' + dl_url)
            except Exception as e:
                print('✗ Asset upload failed: ' + str(e))
        else:
            print('⚠ Installer upload skipped (file or URL not available)')
except urllib.error.HTTPError as e:
    if e.code == 422:
        print('⚠ Release already exists for this tag')
        print('  Visit: https://github.com/anshubhawsar/Hamlab_controller/releases/tag/v2.4.9')
    else:
        print('✗ Release creation failed: HTTP ' + str(e.code))
        print('  Response: ' + e.read().decode())
except Exception as e:
    print('✗ Error: ' + str(e))
