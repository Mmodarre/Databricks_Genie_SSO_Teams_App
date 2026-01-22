# Uploading the Teams App

After running `deploy-full.sh`, you'll have a `teams-app.zip` file ready to upload to Microsoft Teams. This guide covers the different methods to make the bot available to users.

---

## Table of Contents

1. [Upload Options Overview](#1-upload-options-overview)
2. [Option A: Sideload for Personal Testing](#2-option-a-sideload-for-personal-testing)
3. [Option B: Upload to Organization App Catalog](#3-option-b-upload-to-organization-app-catalog)
4. [Option C: Request IT Admin Upload](#4-option-c-request-it-admin-upload)
5. [Verifying the Upload](#5-verifying-the-upload)
6. [Troubleshooting](#6-troubleshooting)

---

## 1. Upload Options Overview

| Method | Who Can Do It | Availability | Best For |
|--------|---------------|--------------|----------|
| **Sideload** | Any user (if enabled) | Only you | Personal testing |
| **Org Catalog** | Teams Admin | Everyone in org | Production deployment |
| **IT Request** | IT Admin | Everyone in org | When you lack admin access |

---

## 2. Option A: Sideload for Personal Testing

Sideloading allows you to test the bot personally before rolling out to your organization.

### Prerequisites

- Sideloading must be enabled for your tenant
- You need the `teams-app.zip` file from deployment

### Check if Sideloading is Enabled

1. Open **Microsoft Teams**
2. Click **Apps** in the left sidebar
3. Click **Manage your apps** at the bottom
4. Look for **Upload an app** option

If you don't see "Upload an app", sideloading is disabled. Contact your IT admin or use Option B/C.

### Steps to Sideload

1. **Open Microsoft Teams** (desktop or web)

2. **Go to Apps**
   - Click the **Apps** icon in the left sidebar

3. **Manage Your Apps**
   - Click **Manage your apps** at the bottom of the Apps page

4. **Upload the App**
   - Click **Upload an app**
   - Select **Upload a custom app**
   - Choose your `teams-app.zip` file

5. **Add the App**
   - Review the app details
   - Click **Add**

6. **Start Using**
   - The bot will appear in your chat list
   - Send a message to start interacting

### Sideload via Direct Link

You can also sideload by navigating directly to:
```
https://teams.microsoft.com/l/app/YOUR_APP_ID
```

Replace `YOUR_APP_ID` with the App ID from your deployment (found in `.env.generated`).

---

## 3. Option B: Upload to Organization App Catalog

This makes the bot available to everyone in your organization. Requires Teams Admin access.

### Prerequisites

- Teams Administrator role (or Global Admin)
- Access to [Teams Admin Center](https://admin.teams.microsoft.com)

### Steps

1. **Open Teams Admin Center**
   - Go to: https://admin.teams.microsoft.com
   - Sign in with your admin account

2. **Navigate to Manage Apps**
   - In the left menu, expand **Teams apps**
   - Click **Manage apps**

3. **Upload the App**
   - Click **+ Upload new app** (top right)
   - Click **Upload**
   - Select your `teams-app.zip` file

4. **Wait for Processing**
   - The app will be validated
   - This may take a few moments

5. **Set App Status**
   - Find your app in the list (search for "Databricks Genie")
   - Ensure **Status** is set to **Allowed**

6. **Configure Availability** (Optional)
   - Click on the app name
   - Go to **Permissions** tab
   - Choose who can install:
     - **Everyone** - All users can install
     - **Specific users/groups** - Limit to certain people

### App Setup Policies (Optional)

To automatically install the app for users:

1. Go to **Teams apps** → **Setup policies**
2. Select a policy (or create new)
3. Under **Installed apps**, click **+ Add apps**
4. Search for "Databricks Genie" and add it
5. Assign the policy to users/groups

---

## 4. Option C: Request IT Admin Upload

If you don't have Teams Admin access, request your IT team to upload the app.

### Information to Provide

Send your IT admin:

1. **The app package**: `teams-app.zip`

2. **App details**:
   - App Name: Databricks Genie Bot
   - Purpose: Query Databricks data via natural language
   - App ID: (from `.env.generated`)

3. **Security information**:
   - Single-tenant (restricted to your org)
   - Uses SSO with user identity
   - No data stored outside Azure/Databricks
   - Credentials in Azure Key Vault

4. **Permissions requested**:
   - `identity` - For SSO authentication
   - `messageTeamMembers` - To send messages

### Sample Request Email

```
Subject: Request to Upload Custom Teams App - Databricks Genie Bot

Hi [IT Admin],

I'd like to request upload of a custom Teams app to our organization catalog.

App Details:
- Name: Databricks Genie Bot
- Purpose: Enables users to query Databricks data using natural language
- Security: Single-tenant, SSO authentication, user identity flow

The app package (teams-app.zip) is attached. Please upload to the Teams Admin 
Center and set status to "Allowed".

Technical contacts for questions: [Your name/email]

Thank you!
```

---

## 5. Verifying the Upload

After uploading, verify the app works correctly:

### Find the App

1. Open **Microsoft Teams**
2. Click **Apps** in the left sidebar
3. Search for **"Databricks Genie"**
4. The app should appear in results

### Test the Bot

1. Click on the app → **Add** (or **Open** if already added)
2. Start a chat with the bot
3. Send any message
4. You should see a sign-in prompt (first time)
5. After signing in, ask a question about your data

### Expected Behavior

- First message: Sign-in card appears
- After sign-in: "You're now signed in" confirmation
- Questions: Bot responds with data/charts from Genie

---

## 6. Troubleshooting

### "Upload an app" Option Not Visible

**Cause**: Sideloading is disabled for your tenant.

**Solutions**:
- Ask IT admin to enable sideloading: Teams Admin Center → Org-wide settings → Custom apps → "Allow interaction with custom apps" = On
- Use Option B (org catalog) instead

### App Upload Fails with Validation Error

**Cause**: Invalid manifest.json

**Solutions**:
1. Verify manifest was generated correctly:
   ```bash
   cat manifest.json | jq .
   ```
2. Check App ID format (should be a GUID)
3. Ensure icons are PNG format, 192x192 pixels
4. Re-run `deploy-full.sh` to regenerate

### App Shows but Bot Doesn't Respond

**Cause**: Bot service or app service issue

**Solutions**:
1. Check bot health:
   ```bash
   curl https://your-app.azurewebsites.net/health
   ```
2. View logs:
   ```bash
   az webapp log tail --name your-app --resource-group your-rg
   ```
3. Verify Bot Service messaging endpoint matches App Service URL

### "App is Blocked" Message

**Cause**: App not allowed in Teams Admin Center

**Solution**: 
1. Go to Teams Admin Center → Manage apps
2. Find the app and set Status to "Allowed"

### Users Can't Find the App

**Cause**: App not in catalog or user lacks permission

**Solutions**:
1. Verify app is uploaded and status is "Allowed"
2. Check app permissions (who can install)
3. Users may need to wait for cache refresh (up to 24 hours)
4. Try direct link: `https://teams.microsoft.com/l/app/YOUR_APP_ID`

---

## Quick Reference

### Teams Admin Center URL
```
https://admin.teams.microsoft.com
```

### Direct App Link Format
```
https://teams.microsoft.com/l/app/{APP_ID}
```

### Useful Azure CLI Commands
```bash
# View deployment info
cat .env.generated

# Check app health
curl https://$(grep APP_NAME .env.generated | cut -d= -f2).azurewebsites.net/health

# View logs
az webapp log tail --name $(grep APP_NAME .env.generated | cut -d= -f2) --resource-group $(grep RESOURCE_GROUP .env.generated | cut -d= -f2)
```

---

*For additional help, see [AZURE_DEPLOYMENT_GUIDE.md](AZURE_DEPLOYMENT_GUIDE.md) or [DATABRICKS_SETUP.md](DATABRICKS_SETUP.md)*
