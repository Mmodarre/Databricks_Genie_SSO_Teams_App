# Databricks Configuration for Teams Genie Bot

This document covers the manual Databricks configuration steps required for the Teams Genie Bot to work with user identity flow.

After running `deploy-full.sh`, complete these steps to enable users to query Databricks via the bot.

---

## Table of Contents

1. [Overview](#1-overview)
2. [User Provisioning](#2-user-provisioning)
3. [Genie Space Access](#3-genie-space-access)
4. [Unity Catalog Permissions](#4-unity-catalog-permissions)
5. [Identity Federation (Optional)](#5-identity-federation-optional)
6. [Verification](#6-verification)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Overview

For the user identity flow to work, three things must be configured in Databricks:

| Requirement | Why | How |
|-------------|-----|-----|
| **Users exist** | Databricks must recognize the user identity | SCIM sync or manual creation |
| **Genie Space access** | Users need permission to query the Genie Space | Share the space with users/groups |
| **Data permissions** | Users need access to underlying tables | Unity Catalog grants |

```
Teams User ──► Azure AD Token ──► Bot ──► Databricks Token ──► Genie API
                                                  │
                                                  ▼
                                        User must exist in
                                        Databricks workspace
                                                  │
                                                  ▼
                                        User must have access
                                        to Genie Space
                                                  │
                                                  ▼
                                        User must have Unity
                                        Catalog permissions
```

---

## 2. User Provisioning

Users must exist in your Databricks workspace with the **same email address** as their Azure AD account.

### Option A: SCIM Provisioning (Recommended)

SCIM (System for Cross-domain Identity Management) automatically syncs users from Azure AD to Databricks.

**Benefits:**
- Automatic user creation/deletion
- Group membership sync
- No manual maintenance

**Setup Steps:**

1. **In Databricks Account Console** (accounts.azuredatabricks.net):
   - Log in as account admin
   - Go to **Settings** → **User provisioning**
   - Click **Set up user provisioning**
   - Copy the **SCIM Token** and **Account SCIM URL**

2. **In Azure Portal** (portal.azure.com):
   - Go to **Azure Active Directory** → **Enterprise applications**
   - Click **New application**
   - Search for **Azure Databricks SCIM Provisioning Connector**
   - Click **Create**

3. **Configure the Enterprise App:**
   - Go to **Provisioning** → **Get started**
   - Set **Provisioning Mode** to **Automatic**
   - Enter:
     - **Tenant URL**: The Account SCIM URL from step 1
     - **Secret Token**: The SCIM Token from step 1
   - Click **Test Connection** to verify
   - Click **Save**

4. **Configure User/Group Assignment:**
   - Go to **Users and groups**
   - Add the users or groups you want to sync
   - Go to **Provisioning** → **Start provisioning**

5. **Verify in Databricks:**
   - Wait 20-40 minutes for initial sync
   - Go to Databricks workspace → **Admin Settings** → **Users**
   - Verify users appear

### Option B: Manual User Creation

For testing or small deployments:

1. Go to your **Databricks Workspace**
2. Click the **Admin Settings** (gear icon in sidebar)
3. Go to **Users**
4. Click **Add User**
5. Enter the user's email address (must match Azure AD email exactly)
6. Click **Add**

**Important:** The email must match exactly. For example:
- Azure AD: `john.doe@company.com`
- Databricks: `john.doe@company.com` (not `johndoe@company.com`)

---

## 3. Genie Space Access

Users need explicit access to the Genie Space they'll query through the bot.

### Grant Access to Individual Users

1. Go to your **Databricks Workspace**
2. Navigate to **Genie** in the sidebar
3. Click on your **Genie Space** (the one with ID from your `.env` file)
4. Click the **Share** button (or permissions icon)
5. In the dialog:
   - Type the user's email address
   - Select the permission level:
     - **Can View**: Can ask questions (recommended for most users)
     - **Can Edit**: Can modify space instructions
     - **Can Manage**: Full control including sharing
6. Click **Add**

### Grant Access to Groups (Recommended)

For easier management, create a group and grant access to the group:

1. **Create a Group:**
   - Go to **Admin Settings** → **Groups**
   - Click **Add Group**
   - Name it (e.g., `genie-users`)
   - Add members

2. **Grant Group Access to Genie Space:**
   - Go to your Genie Space
   - Click **Share**
   - Type the group name
   - Select **Can View**
   - Click **Add**

---

## 4. Unity Catalog Permissions

The user identity flow means queries run with the user's actual permissions. Users need access to the underlying data.

### Required Permissions

| Permission | Level | Purpose |
|------------|-------|---------|
| `USE CATALOG` | Catalog | Access the catalog |
| `USE SCHEMA` | Schema | Access schemas within the catalog |
| `SELECT` | Table/View | Read data from tables |

### Check Current Permissions

Run these SQL commands in Databricks SQL to check permissions:

```sql
-- Show all grants on a catalog
SHOW GRANTS ON CATALOG your_catalog_name;

-- Show grants for a specific user
SHOW GRANTS TO `user@domain.com`;

-- Show grants for a group
SHOW GRANTS TO `group_name`;

-- Check if user can access a specific table
SHOW GRANTS ON TABLE your_catalog.your_schema.your_table;
```

### Grant Permissions

**Option 1: Grant to Individual Users**

```sql
-- Grant catalog access
GRANT USE CATALOG ON CATALOG your_catalog TO `user@domain.com`;

-- Grant schema access
GRANT USE SCHEMA ON SCHEMA your_catalog.your_schema TO `user@domain.com`;

-- Grant table read access
GRANT SELECT ON TABLE your_catalog.your_schema.your_table TO `user@domain.com`;

-- Or grant access to all tables in a schema
GRANT SELECT ON SCHEMA your_catalog.your_schema TO `user@domain.com`;
```

**Option 2: Grant to Groups (Recommended)**

```sql
-- Create a group (if not using SCIM)
-- Note: Groups are typically created in Admin Settings, not SQL

-- Grant catalog access to group
GRANT USE CATALOG ON CATALOG your_catalog TO `genie-users`;

-- Grant schema access to group
GRANT USE SCHEMA ON SCHEMA your_catalog.your_schema TO `genie-users`;

-- Grant read access to all tables in schema
GRANT SELECT ON SCHEMA your_catalog.your_schema TO `genie-users`;
```

### Best Practices

1. **Use Groups**: Manage permissions via groups, not individual users
2. **Least Privilege**: Grant only necessary permissions
3. **Schema-Level Grants**: Prefer schema-level grants over individual table grants
4. **Document Permissions**: Keep track of what groups have access to what data

---

## 5. Identity Federation (Optional)

Identity federation is only needed in advanced scenarios where you're using direct Azure AD tokens instead of the OBO (On-Behalf-Of) flow.

**When you need federation:**
- Direct Azure AD token authentication to Databricks
- Custom authentication flows

**When you DON'T need federation:**
- Standard OBO flow (bot exchanges Teams token for Databricks token)
- This is the default setup in the Genie Bot

### Configure Federation (If Needed)

1. Go to **Databricks Account Console** (accounts.azuredatabricks.net)
2. Navigate to **Settings** → **Identity federation**
3. Click **Add identity provider**
4. Configure:
   ```json
   {
     "issuer": "https://login.microsoftonline.com/{YOUR_TENANT_ID}/v2.0",
     "audiences": ["2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"],
     "subject_claim": "sub"
   }
   ```
5. Click **Save**

---

## 6. Verification

After completing the setup, verify everything works:

### Test User Access

1. **Log in as the test user** to Databricks workspace
2. Navigate to the **Genie Space**
3. Try asking a question
4. Verify the query runs successfully

### Test via Bot

1. Open **Microsoft Teams**
2. Find the **Databricks Genie** bot
3. Send a message (e.g., "How many records are in the sales table?")
4. Verify you get a response

### Check Audit Logs

In Databricks, verify queries are attributed to the correct user:

1. Go to **SQL Warehouses**
2. Click on the warehouse used by Genie
3. Go to **Query History**
4. Verify queries show the user's email, not a service account

---

## 7. Troubleshooting

### Error: "User not found"

**Symptoms:**
- Bot returns "Authentication failed"
- Databricks API returns 403

**Solutions:**
1. Verify user exists in Databricks workspace
2. Check email addresses match exactly (case-sensitive)
3. If using SCIM, wait for sync or trigger manual sync

### Error: "Access denied to Genie Space"

**Symptoms:**
- User can log in but can't access Genie
- "You don't have permission" message

**Solutions:**
1. Share the Genie Space with the user/group
2. Verify the user is in the correct group
3. Check Genie Space permissions in UI

### Error: "Query failed - insufficient permissions"

**Symptoms:**
- User can access Genie but queries fail
- "Access denied to table" error

**Solutions:**
1. Grant Unity Catalog permissions (see Section 4)
2. Check if user needs `USE CATALOG` and `USE SCHEMA`
3. Verify `SELECT` permission on specific tables

### Error: "Token exchange failed"

**Symptoms:**
- Bot can't authenticate user
- OBO flow fails

**Solutions:**
1. Verify Azure AD app has Databricks API permission
2. Check admin consent was granted
3. Verify Databricks OAuth app credentials in Key Vault

### Checking User Identity

To verify which identity Databricks sees:

```sql
-- Run as the user
SELECT current_user();
```

This should return the user's email address, not a service account.

---

## Quick Reference

### Minimum Setup Checklist

- [ ] User exists in Databricks (email matches Azure AD)
- [ ] User has access to Genie Space (Can View or higher)
- [ ] User has `USE CATALOG` permission
- [ ] User has `USE SCHEMA` permission
- [ ] User has `SELECT` permission on required tables

### SQL Commands Cheat Sheet

```sql
-- Check current user
SELECT current_user();

-- List user's permissions
SHOW GRANTS TO `user@domain.com`;

-- Grant full read access to a schema
GRANT USE CATALOG ON CATALOG my_catalog TO `user@domain.com`;
GRANT USE SCHEMA ON SCHEMA my_catalog.my_schema TO `user@domain.com`;
GRANT SELECT ON SCHEMA my_catalog.my_schema TO `user@domain.com`;
```

### Useful Links

- [Databricks SCIM Provisioning](https://docs.databricks.com/administration-guide/users-groups/scim/index.html)
- [Unity Catalog Privileges](https://docs.databricks.com/data-governance/unity-catalog/manage-privileges/privileges.html)
- [Genie Documentation](https://docs.databricks.com/genie/index.html)

---

*For Azure deployment instructions, see [AZURE_DEPLOYMENT_GUIDE.md](AZURE_DEPLOYMENT_GUIDE.md)*
