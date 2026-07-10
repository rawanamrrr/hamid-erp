# 📜 License & Activation System Guide

## Overview
Complete offline-first licensing system for your POS/ERP application!

---

## 🔑 Developer Tools

### Option 1: Web-Based Admin
- **URL:** http://localhost:8000/licensing/admin/
- Create master accounts
- Generate activation tokens from the web interface

### Option 2: Standalone CLI Tool (OFFLINE USE)
Run the standalone token generator without needing the Django server:

```bash
# Navigate to your project directory
cd C:\Users\Admin\Desktop\wholesale-pos-system\v4

# Run the developer CLI tool
.\venv\Scripts\python.exe dev_token_tool.py
```

---

## 📋 Available Token Actions
| Action | Value | Description |
|--------|-------|-------------|
| `CHANGE_STORE_TYPE` | `grocery`, `pharmacy`, `clothes`, `electronics`, `general` | Change the customer's market type |
| `EXTEND_SUBSCRIPTION` | Number (days) | Extend the subscription period |
| `TOGGLE_DARK_MODE` | `true` / `false` | Enable/disable dark mode |
| `ENABLE_MODULE` | Module name | Add a new module |
| `DISABLE_MODULE` | Module name | Remove a module |
| `DEVICE_AUTHORIZATION` | Device info | Authorize a new device |
| `GENERAL_SYSTEM_OVERRIDE` | JSON/text | Advanced override |

---

## 🧑‍💼 Customer Activation
Customers activate tokens here:
- **URL:** http://localhost:8000/licensing/activate/
- They will see their **Master Account ID** on this page
- Paste the token to apply changes
- **100% offline capable!**

---

## 📊 Data Models
1. **MasterStore:** Developer-side customer management
2. **SystemLicense:** Client-side license storage (local per POS)
3. **TokenLog:** Audit log of all generated tokens
4. **Device:** Device authorization tracking
