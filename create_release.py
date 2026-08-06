#!/usr/bin/env python3
import urllib.request
import json
import os

# Configuration
REPO = 'anshubhawsar/Hamlab_controller'
TAG = 'v2.4.5'
INSTALLER_PATH = r'c:\Users\Aanshu Bhawsar\Desktop\Ham_lab_controller\OneClick_Installer.exe'
TOKEN = os.environ.get('GITHUB_TOKEN', '')

if not TOKEN:
    print('GITHUB_TOKEN not set. Automatic release creation skipped.')
    print('\nTo create the release manually, visit:')
    print('https://github.com/anshubhawsar/Hamlab_controller/releases/tag/v2.4.5')
    exit(0)

# Release data
release_data = {
    'tag_name': TAG,
    'name': 'HAM LAB v2.4.5 - Premium UI & Dark Mode Release',
    'body': '''## 🎨 Premium UI & Dark Mode Release

### ✨ Major Features
- **Premium Dark/Light Themes**: Professionally designed with classic colors
- **Perfect Input Visibility**: Theme-aware colors with optimal contrast
- **Production-Level Layout**: Enhanced spacing and typography
- **Dark Mode Matplotlib**: Charts render perfectly in dark mode
- **Accessibility**: Scaling 1.18× widget, 1.06× window

### 🎯 UI Improvements
✓ Navigation bar with button states
✓ Enhanced card styling and shadows
✓ Perfect input field contrast both modes
✓ Fixed overlapping elements
✓ Comparison matrix dark mode support
✓ All welding parameters visible
✓ Improved typography throughout

### 🔧 Technical
- Global theme palette system
- Theme-aware matplotlib figures
- Safe dark mode toggle (subprocess)
- Theme persistence via environment
- Production-ready build

**Version**: 2.4.5 | **Quality**: Enterprise Level | **Status**: Production Ready
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
        print('  Visit: https://github.com/anshubhawsar/Hamlab_controller/releases/tag/v2.4.5')
    else:
        print('✗ Release creation failed: HTTP ' + str(e.code))
        print('  Response: ' + e.read().decode())
except Exception as e:
    print('✗ Error: ' + str(e))
