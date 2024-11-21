# True Markets Tools

This repository provides tools to assist clients during testing and onboarding with True Markets. The tools are organized into `custodian` and `exchange` to help aid in testing and onboarding.

---

## Tools Overview

### **Custodian**
- **`paxos.py`**: A script for handling custodian-related tasks. Details are available in the `custodian/README.md`.

### **Exchange**
#### FIX Protocol
- **`download_spec.py`**: Script to download the TrueX FIX specifications.
- **`fix_client.py`**: A sample FIX client implementation.
- **Configuration Files**:
  - `fix_config_dev.cfg`: Configuration for the development environment.
  - `fix_config_uat.cfg`: Configuration for the UAT environment.

#### REST Protocol
- **`get_client_ids.py`**: A script to retrieve client IDs.
- Refer to `exchange/rest/README.md` for further details.

---

## Getting Started

### Clone the Repository
```
git clone https://github.com/true-markets/tools.git
cd tools
```

### Requirements
- Python 3.x

### Run a Script
Navigate to the appropriate folder and execute the desired script. For example:

```
python exchange/rest/get_client_ids.py
```
