import requests
import os

# Base URL for raw content on GitHub
base_url = "https://raw.githubusercontent.com/true-markets/specification/develop/"

# List of XML files to download
xml_files = [
    "TrueX_FIX50SP2.xml",
    "TrueX_FIXT11.xml"
]

# Directory to save the downloaded files
save_dir = "."
os.makedirs(save_dir, exist_ok=True)

for xml_file in xml_files:
    file_url = base_url + xml_file
    response = requests.get(file_url)

    if response.status_code == 200:
        file_path = os.path.join(save_dir, xml_file)
        with open(file_path, 'wb') as file:
            file.write(response.content)
        print(f"Downloaded and saved: {xml_file}")
    else:
        print(f"Failed to download: {xml_file} (Status code: {response.status_code})")

