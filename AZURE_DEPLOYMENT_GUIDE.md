# Deploying Databricks Genie Bot in Microsoft Teams with SSO

A comprehensive guide to deploy a Databricks Genie bot in Microsoft Teams using Single Sign-On (SSO) authentication with user identity flow.

---

## Table of Contents

1. [Solution Overview](#1-solution-overview)
2. [Architecture & Authentication Flow](#2-architecture--authentication-flow)
3. [Prerequisites](#3-prerequisites)
4. [Azure AD App Registration](#4-azure-ad-app-registration)
5. [Azure Bot Service](#5-azure-bot-service)
6. [Azure Key Vault](#6-azure-key-vault)
7. [Azure App Service](#7-azure-app-service)
8. [Databricks Configuration](#8-databricks-configuration)
9. [Teams App Manifest](#9-teams-app-manifest)
10. [Application Code Configuration](#10-application-code-configuration)
11. [Common Configuration Mistakes](#11-common-configuration-mistakes)
12. [Deployment Checklist](#12-deployment-checklist)
13. [Testing & Verification](#13-testing--verification)
14. [Troubleshooting](#14-troubleshooting)
15. [Security Best Practices](#15-security-best-practices)

---

## 1. Solution Overview

### What This Solution Does

This solution enables users to query their Databricks data using natural language through a Microsoft Teams bot. The key feature is **user identity flow** - queries run with the user's own Databricks permissions, not a shared service account.

### Why User Identity Matters

| Approach | How It Works | Pros | Cons |
|----------|--------------|------|------|
| **Service Principal** | All queries run as a single identity | Simple setup | No per-user permissions, audit trails show bot identity |
| **User Identity (SSO)** | Queries run as the actual user | Per-user permissions, proper audit trails, data governance | More complex setup |

This guide implements **User Identity with SSO** for proper enterprise data governance.

### Components Required

| Component | Purpose |
|-----------|---------|
| **Azure AD App Registration** | Identity provider - authenticates users and enables token exchange |
| **Azure Bot Service** | Routes messages between Teams and your application |
| **Azure Key Vault** | Securely stores credentials (app secrets, Databricks tokens) |
| **Azure App Service** | Hosts the bot application code |
| **Teams App Package** | Defines how the bot appears in Teams |

---

## 2. Architecture & Authentication Flow

### How SSO Authentication Works

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Teams SSO + OBO Authentication Flow                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────┐         ┌─────────┐         ┌─────────┐         ┌───────────┐ │
│  │  User   │         │  Teams  │         │   Bot   │         │ Azure AD  │ │
│  │         │         │ Client  │         │         │         │           │ │
│  └────┬────┘         └────┬────┘         └────┬────┘         └─────┬─────┘ │
│       │                   │                   │                     │       │
│       │ 1. Send message   │                   │                     │       │
│       │──────────────────▶│                   │                     │       │
│       │                   │                   │                     │       │
│       │                   │ 2. Forward to bot │                     │       │
│       │                   │──────────────────▶│                     │       │
│       │                   │                   │                     │       │
│       │                   │                   │ 3. No token cached  │       │
│       │                   │                   │    Send OAuthCard   │       │
│       │                   │◀──────────────────│                     │       │
│       │                   │                   │                     │       │
│       │                   │ 4. Request token silently (user already │       │
│       │                   │    logged into Teams/Azure AD)          │       │
│       │                   │────────────────────────────────────────▶│       │
│       │                   │                   │                     │       │
│       │                   │                   │    5. Return token  │       │
│       │                   │◀────────────────────────────────────────│       │
│       │                   │                   │                     │       │
│       │                   │ 6. signin/tokenExchange invoke          │       │
│       │                   │   (contains user's access token)        │       │
│       │                   │──────────────────▶│                     │       │
│       │                   │                   │                     │       │
│       │                   │                   │ 7. OBO exchange:    │       │
│       │                   │                   │    Teams token →    │       │
│       │                   │                   │    Databricks token │       │
│       │                   │                   │────────────────────▶│       │
│       │                   │                   │                     │       │
│       │                   │                   │◀────────────────────│       │
│       │                   │                   │ 8. Databricks token │       │
│       │                   │                   │                     │       │
│       │                   │                   │ 9. Call Genie API   │       │
│       │                   │                   │    with user token  │       │
│       │                   │                   │──────────────────▶ Databricks
│       │                   │                   │                     │       │
│       │                   │ 10. Return results│                     │       │
│       │◀──────────────────│◀──────────────────│                     │       │
│       │                   │                   │                     │       │
│  └─────────┘         └─────────┘         └─────────┘         └───────────┘ │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Key Concepts

| Term | Definition |
|------|------------|
| **SSO (Single Sign-On)** | User is already logged into Teams/Azure AD, so they don't need to enter credentials again |
| **OAuthCard** | A special card sent by the bot that tells Teams to request a token |
| **Token Exchange (signin/tokenExchange)** | Teams sends the user's token to the bot via an invoke activity |
| **OBO (On-Behalf-Of)** | Azure AD flow that exchanges one token (Teams) for another token (Databricks) while maintaining user identity |

### Resource Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Azure Resources                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     Azure AD App Registration                        │    │
│  │                                                                      │    │
│  │  WHY: This is the identity for your bot. It allows:                 │    │
│  │  • Bot Framework to authenticate your bot                           │    │
│  │  • Teams to request tokens on behalf of users (SSO)                 │    │
│  │  • OBO flow to exchange Teams tokens for Databricks tokens          │    │
│  │                                                                      │    │
│  │  KEY SETTINGS:                                                       │    │
│  │  • Application ID URI: api://botid-{APP_ID}                         │    │
│  │  • Scope: access_as_user (allows Teams to request tokens)           │    │
│  │  • API Permission: Azure Databricks user_impersonation              │    │
│  │  • Redirect URI: https://token.botframework.com/.auth/web/redirect │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                        Azure Bot Service                             │    │
│  │                                                                      │    │
│  │  WHY: Routes messages between Teams and your application.           │    │
│  │  • Handles the Bot Framework protocol                               │    │
│  │  • Manages the Teams channel connection                             │    │
│  │  • Stores OAuth connection settings for token exchange              │    │
│  │                                                                      │    │
│  │  KEY SETTINGS:                                                       │    │
│  │  • Messaging Endpoint: https://{app}.azurewebsites.net/api/messages │    │
│  │  • OAuth Connection: TeamsSSO (for token exchange)                  │    │
│  │  • Token Exchange URL: api://botid-{APP_ID} (MUST MATCH AD URI)     │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│          ┌─────────────────────────┴─────────────────────────┐              │
│          ▼                                                   ▼              │
│  ┌──────────────────────┐                      ┌──────────────────────┐     │
│  │     Key Vault        │                      │    App Service       │     │
│  │                      │                      │                      │     │
│  │  WHY: Secure storage │                      │  WHY: Hosts your bot │     │
│  │  for secrets. Never  │─────────────────────▶│  application code.   │     │
│  │  hardcode credentials│   (Managed Identity) │  Runs Python/Node.   │     │
│  │  in code or config.  │                      │                      │     │
│  │                      │                      │  Receives messages   │     │
│  │  STORES:             │                      │  from Bot Service,   │     │
│  │  • App ID & Secret   │                      │  processes them,     │     │
│  │  • Databricks creds  │                      │  calls Databricks    │     │
│  │  • Tenant ID         │                      │  Genie API.          │     │
│  └──────────────────────┘                      └──────────────────────┘     │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Prerequisites

### Required Access & Permissions

| Access | Why Needed |
|--------|------------|
| Azure subscription (Owner/Contributor) | Create Azure resources |
| Azure AD Global Admin or Application Admin | Create app registrations, grant admin consent |
| Microsoft 365 tenant | Required for Teams |
| Teams Admin Center access | Upload and approve Teams app |
| Databricks workspace access | Create Genie Space, configure OAuth |

### Information to Gather Before Starting

| Item | Where to Find It | Example |
|------|------------------|---------|
| Azure AD Tenant ID | Azure Portal → Azure AD → Overview | `6c239608-a51e-47a1-81d7-c497cbdf6c4d` |
| Databricks Workspace URL | Databricks workspace browser URL | `https://adb-123456789.12.azuredatabricks.net` |
| Genie Space ID | Databricks → Genie → Space settings | `01efabcd-1234-5678-abcd-ef1234567890` |
| Databricks OAuth App credentials | Databricks → Settings → OAuth apps | Client ID and Secret |

### Tools Required

- **Azure CLI** - `az login` authenticated
- **Python 3.11+** - For local testing
- **zip** utility - For creating deployment packages

---

## 4. Azure AD App Registration

### Why This Component?

The Azure AD App Registration serves three critical purposes:

1. **Bot Identity**: The Bot Framework uses this to authenticate your bot when sending/receiving messages
2. **Teams SSO**: Allows Teams to silently request tokens for users (no login prompt needed)
3. **OBO Token Exchange**: Enables exchanging the Teams token for a Databricks-scoped token

### 4.1 Create App Registration

**Via Azure Portal:**
1. Navigate to **Azure Portal** → **Azure Active Directory** → **App registrations**
2. Click **New registration**
3. Configure:
   - **Name**: `Databricks-Genie-Bot` (or your preferred name)
   - **Supported account types**: `Accounts in this organizational directory only (Single tenant)`
   - **Redirect URI**: Leave blank for now (we'll add it later)
4. Click **Register**
5. **Copy the Application (client) ID** - you'll need this in many places

**Via Azure CLI:**
```bash
# Create the app registration
az ad app create \
  --display-name "Databricks-Genie-Bot" \
  --sign-in-audience "AzureADMyOrg"

# Get the App ID (save this!)
APP_ID=$(az ad app list --display-name "Databricks-Genie-Bot" --query "[0].appId" -o tsv)
echo "App ID: $APP_ID"
```

### 4.2 Set Application ID URI

**Why**: This URI uniquely identifies your app for token requests. Teams SSO specifically requires the format `api://botid-{APP_ID}`.

**⚠️ CRITICAL**: The URI **MUST** start with `api://botid-` for Teams SSO to work. This is a Teams requirement.

**Via Azure Portal:**
1. Go to your App Registration → **Expose an API**
2. Click **Set** next to "Application ID URI"
3. Enter: `api://botid-{YOUR_APP_ID}`
   - Example: `api://botid-90c542b7-b31f-4f95-8ab2-0faee6e916db`
4. Click **Save**

**Via Azure CLI:**
```bash
az ad app update \
  --id $APP_ID \
  --identifier-uris "api://botid-$APP_ID"

# Verify
az ad app show --id $APP_ID --query "identifierUris" -o json
```

### 4.3 Add `access_as_user` Scope

**Why**: This scope allows Teams to request access tokens on behalf of users. When Teams sees this scope, it knows it can use SSO.

**Via Azure Portal:**
1. In **Expose an API** → Click **Add a scope**
2. Configure:
   - **Scope name**: `access_as_user`
   - **Who can consent**: `Admins and users`
   - **Admin consent display name**: `Access Databricks Genie as user`
   - **Admin consent description**: `Allow the application to access Databricks Genie on behalf of the signed-in user.`
   - **User consent display name**: `Access Databricks Genie`
   - **User consent description**: `Allow the application to access Databricks Genie on your behalf.`
   - **State**: `Enabled`
3. Click **Add scope**

**Via Azure CLI:**
```bash
# Generate a UUID for the scope
SCOPE_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')

az ad app update --id $APP_ID --set api="{
  \"requestedAccessTokenVersion\": 2,
  \"oauth2PermissionScopes\": [
    {
      \"id\": \"$SCOPE_ID\",
      \"adminConsentDescription\": \"Allow the application to access Databricks Genie on behalf of the signed-in user.\",
      \"adminConsentDisplayName\": \"Access Databricks Genie as user\",
      \"isEnabled\": true,
      \"type\": \"User\",
      \"userConsentDescription\": \"Allow the application to access Databricks Genie on your behalf.\",
      \"userConsentDisplayName\": \"Access Databricks Genie\",
      \"value\": \"access_as_user\"
    }
  ]
}"
```

### 4.4 Pre-Authorize Teams Client Applications

**Why**: Pre-authorizing the Teams clients means users won't see a consent prompt every time. Teams can silently request tokens.

**Via Azure Portal:**
1. In **Expose an API** → **Authorized client applications**
2. Click **Add a client application**
3. Add these Teams client IDs one at a time:

| Client ID | Application | Why Needed |
|-----------|-------------|------------|
| `1fec8e78-bce4-4aaf-ab1b-5451cc387264` | Teams mobile/desktop client | Users on desktop/mobile apps |
| `5e3ce6c0-2b1f-4285-8d4b-75ee78787346` | Teams web client | Users on teams.microsoft.com |

4. For each client, check the `access_as_user` scope
5. Click **Add application**

### 4.5 Add Databricks API Permission

**Why**: This permission allows the OBO flow to exchange the Teams token for a Databricks token. Without this, the bot can't call Databricks on behalf of the user.

**Via Azure Portal:**
1. Go to **API permissions** → Click **Add a permission**
2. Click **APIs my organization uses**
3. Search for `Azure Databricks` (or use ID: `2ff814a6-3304-4ab8-85cb-cd0e6f879c1d`)
4. Select **Delegated permissions** (not Application permissions!)
5. Check `user_impersonation`
6. Click **Add permissions**
7. Click **Grant admin consent for {your organization}**

**Via Azure CLI:**
```bash
# Databricks Resource ID (this is a fixed value for Azure Databricks)
DATABRICKS_RESOURCE_ID="2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"

# user_impersonation scope ID (this is also a fixed value)
DATABRICKS_SCOPE_ID="b8d88131-27e4-4e92-a55f-dc714bd18b45"

# Add the permission
az ad app permission add \
  --id $APP_ID \
  --api $DATABRICKS_RESOURCE_ID \
  --api-permissions "$DATABRICKS_SCOPE_ID=Scope"

# Grant admin consent
az ad app permission grant \
  --id $APP_ID \
  --api $DATABRICKS_RESOURCE_ID \
  --scope "user_impersonation"
```

### 4.6 Configure Redirect URI (Return URL)

**Why**: When OAuth authentication completes, Azure AD needs to redirect back somewhere. The Bot Framework uses a standard URL for this. Without this, OAuth sign-in will fail.

**Via Azure Portal:**
1. Go to **Authentication** → **Platform configurations**
2. Click **Add a platform** → **Web**
3. Add Redirect URI:
   ```
   https://token.botframework.com/.auth/web/redirect
   ```
4. Click **Configure**

**Via Azure CLI:**
```bash
az ad app update \
  --id $APP_ID \
  --web-redirect-uris "https://token.botframework.com/.auth/web/redirect"
```

### 4.7 Create Client Secret

**Why**: The bot application needs credentials to prove its identity when requesting tokens.

**Via Azure Portal:**
1. Go to **Certificates & secrets** → **Client secrets**
2. Click **New client secret**
3. Enter description: `bot-secret`
4. Select expiration (recommended: 12 months, set calendar reminder to rotate)
5. Click **Add**
6. **⚠️ COPY THE SECRET VALUE IMMEDIATELY** - it won't be shown again!

**Via Azure CLI:**
```bash
az ad app credential reset \
  --id $APP_ID \
  --display-name "bot-secret" \
  --years 1

# Output includes password - SAVE IT!
```

### 4.8 Create Service Principal

**Why**: A Service Principal is the "instance" of the app in your tenant. It's required for the app to function.

```bash
az ad sp create --id $APP_ID
```

### 4.9 Verify Token Version (Optional)

**Why**: Azure AD can issue v1.0 or v2.0 tokens. The OBO flow works best with v2.0 tokens.

**Via Azure Portal:**
1. Go to your App Registration → **Manifest**
2. Find `accessTokenAcceptedVersion`
3. Ensure it's set to `2`:
   ```json
   "accessTokenAcceptedVersion": 2
   ```
4. Click **Save**

If it's `null`, the app accepts both versions (which is usually fine). Setting it to `2` explicitly ensures v2.0 tokens.

### 4.10 Azure AD Configuration Summary

| Setting | Value | Location in Portal |
|---------|-------|-------------------|
| Application ID URI | `api://botid-{APP_ID}` | Expose an API |
| Scope | `access_as_user` | Expose an API → Scopes |
| Authorized Clients | Teams client IDs (see 4.4) | Expose an API → Authorized client applications |
| Redirect URI | `https://token.botframework.com/.auth/web/redirect` | Authentication → Web |
| API Permission | `Azure Databricks - user_impersonation` (Delegated) | API permissions |
| Client Secret | (generated) | Certificates & secrets |

---

## 5. Azure Bot Service

### Why This Component?

Azure Bot Service is the **bridge between Microsoft Teams and your application**:

1. **Message Routing**: Teams doesn't talk directly to your app - it sends messages to Bot Service, which forwards them to your endpoint
2. **Authentication**: Bot Service validates that incoming messages really came from Teams
3. **OAuth Management**: Stores OAuth connection settings used for token exchange
4. **Channel Management**: Handles the specifics of Teams channel (card rendering, invoke activities, etc.)

### 5.1 Create Azure Bot

**Via Azure Portal:**
1. Go to **Azure Portal** → **Create a resource**
2. Search for "Azure Bot" → Click **Create**
3. Configure:
   - **Bot handle**: `your-genie-bot` (must be globally unique)
   - **Subscription**: Select your subscription
   - **Resource group**: Create new or select existing
   - **Pricing tier**: `Standard` (or F0 for free tier during development)
   - **Type of App**: `Single Tenant` (recommended for enterprise)
   - **Creation type**: `Use existing app registration`
   - **App ID**: Enter the App ID from Step 4
   - **App tenant ID**: Enter your Tenant ID
4. Click **Review + create** → **Create**

**Via Azure CLI:**
```bash
az bot create \
  --resource-group $RESOURCE_GROUP \
  --name "your-genie-bot" \
  --kind "azurebot" \
  --app-type "SingleTenant" \
  --appid $APP_ID \
  --tenant-id $TENANT_ID \
  --endpoint "https://your-app.azurewebsites.net/api/messages"
```

### 5.2 Configure Messaging Endpoint

**Why**: This tells Bot Service where to send incoming messages. It must point to your App Service.

**Via Azure Portal:**
1. Go to **Bot Services** → Your bot → **Configuration**
2. Set **Messaging endpoint**:
   ```
   https://{your-app-service-name}.azurewebsites.net/api/messages
   ```
3. Click **Apply**

### 5.3 Enable Teams Channel

**Why**: Bot Service supports many channels (Slack, SMS, web chat, etc.). You must explicitly enable Teams.

**Via Azure Portal:**
1. Go to **Bot Services** → Your bot → **Channels**
2. Click **Microsoft Teams**
3. Click **Apply** (accept terms if prompted)

**Via Azure CLI:**
```bash
az bot msteams create \
  --name "your-genie-bot" \
  --resource-group $RESOURCE_GROUP
```

### 5.4 Configure OAuth Connection Settings

**Why**: This is where you configure the SSO token exchange. When your bot sends an OAuthCard, Bot Service uses these settings to request/exchange tokens.

**⚠️ CRITICAL: The Token Exchange URL MUST exactly match the Application ID URI from Azure AD.**

**Via Azure Portal:**
1. Go to **Bot Services** → Your bot → **Configuration**
2. Under **OAuth Connection Settings**, click **Add setting**
3. Configure:

| Field | Value | Why |
|-------|-------|-----|
| **Name** | `TeamsSSO` | Your code references this name |
| **Service Provider** | `Azure Active Directory v2` | Use v2 for modern auth |
| **Client ID** | Your App ID from Step 4 | Identifies your app |
| **Client Secret** | Your client secret from Step 4.7 | Proves your app's identity |
| **Token Exchange URL** | `api://botid-{APP_ID}` | **MUST match Application ID URI exactly!** |
| **Tenant ID** | Your Tenant ID | Restricts to your organization |
| **Scopes** | `openid profile User.Read` | Basic user info scopes |

4. Click **Save**
5. Click **Test Connection** to verify it works

**Via Azure CLI:**
```bash
az bot authsetting create \
  --resource-group $RESOURCE_GROUP \
  --name "your-genie-bot" \
  --setting-name "TeamsSSO" \
  --client-id $APP_ID \
  --client-secret "YOUR_CLIENT_SECRET" \
  --service "Aadv2" \
  --provider-scope-string "openid profile User.Read" \
  --parameters \
    tenantId=$TENANT_ID \
    tokenExchangeUrl="api://botid-$APP_ID"
```

### 5.5 Bot Service Configuration Summary

| Setting | Value | Why It Matters |
|---------|-------|----------------|
| App Type | Single Tenant | More secure, restricted to your org |
| App ID | Same as Azure AD App | Links bot to your identity |
| Messaging Endpoint | `https://{app}.azurewebsites.net/api/messages` | Where messages are sent |
| Teams Channel | Enabled | Allows Teams to communicate |
| OAuth Connection Name | `TeamsSSO` | Referenced in code |
| Token Exchange URL | `api://botid-{APP_ID}` | **MUST match AD URI** |

---

## 6. Azure Key Vault

### Why This Component?

**Never hardcode secrets in your application or configuration files.**

Key Vault provides:
- Secure, encrypted storage for secrets
- Access control via Azure AD
- Audit logging of secret access
- Secret rotation support

### 6.1 Create Key Vault

```bash
az keyvault create \
  --name "your-genie-bot-kv" \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --sku standard
```

### 6.2 Store Required Secrets

```bash
KV_NAME="your-genie-bot-kv"

# Bot Framework credentials (from Azure AD App Registration)
az keyvault secret set --vault-name $KV_NAME \
  --name "microsoft-app-id" \
  --value "YOUR_APP_ID"

az keyvault secret set --vault-name $KV_NAME \
  --name "microsoft-app-password" \
  --value "YOUR_CLIENT_SECRET"

az keyvault secret set --vault-name $KV_NAME \
  --name "microsoft-app-tenant-id" \
  --value "YOUR_TENANT_ID"

# Databricks credentials (for OBO token exchange and Genie API)
az keyvault secret set --vault-name $KV_NAME \
  --name "databricks-host" \
  --value "https://adb-XXXXXXXXX.XX.azuredatabricks.net"

az keyvault secret set --vault-name $KV_NAME \
  --name "databricks-client-id" \
  --value "YOUR_DATABRICKS_CLIENT_ID"

az keyvault secret set --vault-name $KV_NAME \
  --name "databricks-client-secret" \
  --value "YOUR_DATABRICKS_CLIENT_SECRET"

az keyvault secret set --vault-name $KV_NAME \
  --name "genie-space-id" \
  --value "YOUR_GENIE_SPACE_ID"
```

### 6.3 Key Vault Secrets Reference

| Secret Name | Source | Purpose |
|-------------|--------|---------|
| `microsoft-app-id` | Azure AD App Registration | Bot authentication |
| `microsoft-app-password` | Azure AD App Registration | Bot authentication |
| `microsoft-app-tenant-id` | Azure AD | Tenant restriction |
| `databricks-host` | Databricks workspace URL | API endpoint |
| `databricks-client-id` | Databricks OAuth app | OBO token exchange |
| `databricks-client-secret` | Databricks OAuth app | OBO token exchange |
| `genie-space-id` | Databricks Genie | Which Genie to query |

---

## 7. Azure App Service

### Why This Component?

App Service hosts your bot application - the Python code that:
- Receives messages from Bot Service
- Handles SSO token exchange
- Calls Databricks Genie API
- Formats and sends responses

### 7.1 Create App Service Plan & Web App

```bash
# Create App Service Plan (B1 is minimum recommended for production)
az appservice plan create \
  --name "genie-bot-plan" \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --is-linux \
  --sku B1

# Create Web App with Python runtime
az webapp create \
  --name "your-genie-bot-app" \
  --resource-group $RESOURCE_GROUP \
  --plan "genie-bot-plan" \
  --runtime "PYTHON:3.12"
```

### 7.2 Configure App Settings

```bash
az webapp config appsettings set \
  --name "your-genie-bot-app" \
  --resource-group $RESOURCE_GROUP \
  --settings \
    KEY_VAULT_URL="https://your-genie-bot-kv.vault.azure.net/" \
    WEBSITES_PORT="8000" \
    SCM_DO_BUILD_DURING_DEPLOYMENT="true"
```

### 7.3 Configure Startup Command

```bash
az webapp config set \
  --name "your-genie-bot-app" \
  --resource-group $RESOURCE_GROUP \
  --startup-file "gunicorn --bind 0.0.0.0:8000 --worker-class aiohttp.GunicornWebWorker app_azure:APP"
```

### 7.4 Enable Managed Identity & Key Vault Access

**Why**: Managed Identity allows your App Service to authenticate to Key Vault without storing any credentials.

```bash
# Enable system-assigned managed identity
az webapp identity assign \
  --name "your-genie-bot-app" \
  --resource-group $RESOURCE_GROUP

# Get the identity's principal ID
IDENTITY_ID=$(az webapp identity show \
  --name "your-genie-bot-app" \
  --resource-group $RESOURCE_GROUP \
  --query "principalId" -o tsv)

# Grant Key Vault access to the managed identity
az keyvault set-policy \
  --name "your-genie-bot-kv" \
  --object-id $IDENTITY_ID \
  --secret-permissions get list
```

### 7.5 Deploy Application Code

```bash
# Create deployment package
zip -r deploy.zip app_azure.py requirements.txt startup.sh icon-color.png icon-outline.png

# Deploy to App Service
az webapp deploy \
  --resource-group $RESOURCE_GROUP \
  --name "your-genie-bot-app" \
  --src-path deploy.zip \
  --type zip
```

---

## 8. Databricks Configuration

### Why This Component?

For user identity flow to work end-to-end, users must:
1. **Exist in Databricks** - The user's identity must be recognized
2. **Have Genie Space access** - Users need permission to query the specific Genie Space
3. **Have appropriate data permissions** - Unity Catalog permissions control data access

Without this, even with valid tokens, Databricks will reject requests.

### 8.1 Ensure Users Exist in Databricks

Users must exist in your Databricks workspace before they can authenticate.

**Option A: SCIM Provisioning (Recommended for Production)**

SCIM automatically syncs users from Azure AD to Databricks:

1. Go to **Databricks Account Console** (accounts.azuredatabricks.net)
2. Navigate to **Settings** → **User provisioning**
3. Configure SCIM connector with Azure AD
4. Users are automatically provisioned when added to the synced group

**Benefits**: Automatic user lifecycle management, group sync, no manual work.

**Option B: Manual User Creation**

For testing or small deployments:

1. Go to your **Databricks Workspace**
2. Click **Admin Settings** (gear icon) → **Users**
3. Click **Add User**
4. Enter the user's email (must match their Azure AD email)
5. Click **Add**

### 8.2 Grant Genie Space Access

Users need explicit access to the Genie Space they'll query:

1. Go to your **Databricks Workspace**
2. Navigate to **Genie** → Select your Space
3. Click **Share** (or permissions icon)
4. Add users or groups with appropriate access level:
   - **Can View**: Can ask questions
   - **Can Edit**: Can modify the space configuration
   - **Can Manage**: Full control

### 8.3 Verify Unity Catalog Permissions

User identity flow means queries run with the user's actual permissions:

- Users need `SELECT` permission on tables they query
- Users need `USE CATALOG` and `USE SCHEMA` permissions
- Permissions are inherited from groups

**Check user permissions:**
```sql
-- In Databricks SQL, as admin
SHOW GRANTS ON CATALOG your_catalog;
SHOW GRANTS TO `user@domain.com`;
```

### 8.4 (Optional) Configure Identity Federation

If you're using direct Azure AD tokens (without OBO), you may need a federation policy:

1. Go to **Databricks Account Console** (accounts.azuredatabricks.net)
2. Navigate to **Settings** → **Identity federation**
3. Create a federation policy:
   ```json
   {
     "issuer": "https://login.microsoftonline.com/{TENANT_ID}/v2.0",
     "audiences": ["2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"],
     "subject": "sub"
   }
   ```

**Note**: This is typically only needed for advanced scenarios. The standard OBO flow (exchanging for a Databricks-scoped token) usually works without federation policies.

### 8.5 Databricks Configuration Checklist

- [ ] Users exist in Databricks workspace (via SCIM or manual)
- [ ] Users have access to the Genie Space
- [ ] Users have Unity Catalog permissions on underlying data
- [ ] (If needed) Federation policy configured

---

## 9. Teams App Manifest

### Why This Component?

The manifest.json file defines how your bot appears and behaves in Teams:
- Bot identity and capabilities
- Required permissions
- SSO configuration (`webApplicationInfo`)
- Valid domains for OAuth redirects

### 8.1 manifest.json Structure

```json
{
  "$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.16/MicrosoftTeams.schema.json",
  "manifestVersion": "1.16",
  "version": "1.0.0",
  "id": "{APP_ID}",
  "packageName": "com.yourcompany.genie",
  "developer": {
    "name": "Your Company",
    "websiteUrl": "https://yourcompany.com",
    "privacyUrl": "https://yourcompany.com/privacy",
    "termsOfUseUrl": "https://yourcompany.com/terms"
  },
  "icons": {
    "color": "icon-color.png",
    "outline": "icon-outline.png"
  },
  "name": {
    "short": "Databricks Genie",
    "full": "Databricks Genie Bot"
  },
  "description": {
    "short": "Query your data with natural language",
    "full": "A bot that connects to Databricks Genie to answer natural language questions about your data."
  },
  "accentColor": "#5C6BC0",
  "bots": [
    {
      "botId": "{APP_ID}",
      "scopes": ["personal", "team", "groupchat"],
      "supportsFiles": false,
      "isNotificationOnly": false
    }
  ],
  "permissions": ["identity", "messageTeamMembers"],
  "validDomains": [
    "{your-app}.azurewebsites.net",
    "token.botframework.com"
  ],
  "webApplicationInfo": {
    "id": "{APP_ID}",
    "resource": "api://botid-{APP_ID}"
  }
}
```

### 8.2 Critical Fields Explained

| Field | Value | Why It Matters |
|-------|-------|----------------|
| `id` | Your App ID | Must match Azure AD App Registration - this is the bot's identity |
| `bots[0].botId` | Your App ID | Must match Azure Bot Service - routes messages correctly |
| `validDomains` | App Service + `token.botframework.com` | Security - limits where OAuth can redirect |
| `webApplicationInfo.id` | Your App ID | Tells Teams which app to request tokens for |
| `webApplicationInfo.resource` | `api://botid-{APP_ID}` | **MUST exactly match Application ID URI in Azure AD** |
| `permissions` | `["identity"]` | Allows app to access user identity for SSO |

### 8.3 Create and Upload Teams App Package

```bash
# Create the zip package (must contain manifest.json and icons at root level)
zip -j teams-app.zip manifest.json icon-color.png icon-outline.png
```

**Upload to Teams Admin Center:**
1. Go to https://admin.teams.microsoft.com
2. Navigate to **Teams apps** → **Manage apps**
3. Click **Upload new app**
4. Select your `teams-app.zip`
5. Once uploaded, ensure the app status is **Allowed**

---

## 10. Application Code Configuration

### 9.1 Key Code Components

Your bot application needs these key components:

**1. Bot Framework Adapter Configuration:**
```python
from botbuilder.core import BotFrameworkAdapterSettings, BotFrameworkAdapter

SETTINGS = BotFrameworkAdapterSettings(
    app_id=APP_ID,                    # From Key Vault
    app_password=APP_PASSWORD,         # From Key Vault
    channel_auth_tenant=APP_TENANT_ID, # Restricts to your tenant
    oauth_endpoint=f"https://login.microsoftonline.com/{APP_TENANT_ID}"
)
ADAPTER = BotFrameworkAdapter(SETTINGS)
```

**2. MSAL Client for OBO Exchange:**
```python
import msal

msal_app = msal.ConfidentialClientApplication(
    client_id=APP_ID,
    client_credential=APP_PASSWORD,
    authority=f"https://login.microsoftonline.com/{TENANT_ID}"
)

# Exchange Teams token for Databricks token
result = msal_app.acquire_token_on_behalf_of(
    user_assertion=teams_token,
    scopes=["2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/user_impersonation"]
)
databricks_token = result["access_token"]
```

**3. Token Exchange Handler:**
```python
async def _handle_token_exchange(self, turn_context, value):
    token = value.get("token")
    if token:
        # Store token for user
        self.user_tokens[user_id] = token
        
        # MUST return InvokeResponse (not dict!)
        await turn_context.send_activity(
            Activity(
                type=ActivityTypes.invoke_response,
                value=InvokeResponse(status=200, body={"id": value.get("id")})
            )
        )
```

**4. OAuthCard for SSO Trigger:**
```python
oauth_card = OAuthCard(
    text="Please sign in to continue",
    connection_name="TeamsSSO",  # Must match Bot Service OAuth connection name
    token_exchange_resource={
        "id": "TeamsSSO",
        "uri": f"api://botid-{APP_ID}"  # Must match Application ID URI
    },
    buttons=[CardAction(type="signin", title="Sign In", value="TeamsSSO")]
)
```

### 9.2 SSO Connection Name

The SSO connection name used in code **must match** the OAuth connection name in Azure Bot Service:

| Location | Value |
|----------|-------|
| Code: `connection_name` | `"TeamsSSO"` |
| Bot Service: OAuth Connection Setting Name | `TeamsSSO` |

---

## 11. Common Configuration Mistakes

### ❌ Mistake 1: Wrong Application ID URI Format

**Symptom**: Token exchange fails, SSO doesn't trigger

**Wrong:**
```
api://90c542b7-b31f-4f95-8ab2-0faee6e916db
```

**Correct:**
```
api://botid-90c542b7-b31f-4f95-8ab2-0faee6e916db
```

**Why**: Teams SSO specifically requires the `botid-` prefix. This is a Microsoft Teams requirement, not a general Azure AD requirement.

---

### ❌ Mistake 2: Token Exchange URL Mismatch

**Symptom**: `TypeError: exchange token returned improper result: <class 'NoneType'>`

The following three values **MUST be identical**:

| Location | Setting | Value |
|----------|---------|-------|
| Azure AD | Application ID URI | `api://botid-{APP_ID}` |
| Bot Service | OAuth Connection → Token Exchange URL | `api://botid-{APP_ID}` |
| manifest.json | `webApplicationInfo.resource` | `api://botid-{APP_ID}` |

---

### ❌ Mistake 3: Missing Redirect URI

**Symptom**: OAuth flow fails with "redirect_uri mismatch" error

**Solution**: Add this redirect URI in Azure AD → Authentication:
```
https://token.botframework.com/.auth/web/redirect
```

---

### ❌ Mistake 4: Missing Authorized Client Applications

**Symptom**: Users see consent prompts every time, or SSO fails silently

**Solution**: In Azure AD → Expose an API, add these Teams clients:
- `1fec8e78-bce4-4aaf-ab1b-5451cc387264` (Teams desktop/mobile)
- `5e3ce6c0-2b1f-4285-8d4b-75ee78787346` (Teams web)

---

### ❌ Mistake 5: Using Dict Instead of InvokeResponse

**Symptom**: `AttributeError: 'dict' object has no attribute 'status'`

**Wrong:**
```python
Activity(type=ActivityTypes.invoke_response, value={"status": 200})
```

**Correct:**
```python
from botbuilder.schema import InvokeResponse
Activity(type=ActivityTypes.invoke_response, value=InvokeResponse(status=200))
```

---

### ❌ Mistake 6: TokenExchangeResource Import Error

**Symptom**: `ImportError: cannot import name 'TokenExchangeResource' from 'botbuilder.schema'`

**Cause**: The `TokenExchangeResource` class doesn't exist in botbuilder-schema 4.14.x

**Solution**: Use a dictionary instead:
```python
# Don't do this - class doesn't exist
from botbuilder.schema import TokenExchangeResource

# Do this instead - use a dictionary
token_exchange_resource = {
    "id": "TeamsSSO",
    "uri": f"api://botid-{APP_ID}"
}
```

---

### ❌ Mistake 7: Databricks Permission Not Granted

**Symptom**: OBO exchange fails, "AADSTS65001" error

**Solution**: 
1. Add the `Azure Databricks` → `user_impersonation` permission
2. **Grant admin consent** (requires admin privileges)

---

## 12. Deployment Checklist

Use this checklist to verify your configuration:

### Azure AD App Registration
- [ ] App created with Single Tenant audience
- [ ] Application ID URI set to `api://botid-{APP_ID}`
- [ ] `access_as_user` scope created and enabled
- [ ] Teams clients pre-authorized (both desktop and web IDs)
- [ ] Redirect URI added: `https://token.botframework.com/.auth/web/redirect`
- [ ] Azure Databricks `user_impersonation` permission added
- [ ] Admin consent granted for Databricks permission
- [ ] Client secret created and saved securely
- [ ] Service Principal created

### Azure Bot Service
- [ ] Bot created with Single Tenant type
- [ ] Messaging endpoint points to App Service `/api/messages`
- [ ] Teams channel enabled
- [ ] OAuth connection "TeamsSSO" created
- [ ] Token Exchange URL matches Application ID URI exactly
- [ ] OAuth connection test passes

### Azure Key Vault
- [ ] Key Vault created
- [ ] All secrets stored (app-id, password, tenant-id, databricks-*)
- [ ] App Service managed identity has access

### Azure App Service
- [ ] App created with Python runtime
- [ ] KEY_VAULT_URL environment variable set
- [ ] Startup command configured
- [ ] Managed identity enabled
- [ ] Application code deployed
- [ ] Health endpoint responds

### Databricks
- [ ] Users exist in Databricks workspace (SCIM or manual)
- [ ] Users have access to the Genie Space
- [ ] Users have Unity Catalog permissions on underlying data
- [ ] (If needed) Federation policy configured

### Teams App
- [ ] manifest.json has correct `id` (matches App ID)
- [ ] manifest.json has correct `botId` (matches App ID)
- [ ] manifest.json has `webApplicationInfo` section
- [ ] `webApplicationInfo.resource` matches Application ID URI
- [ ] `validDomains` includes App Service and `token.botframework.com`
- [ ] App package uploaded to Teams Admin Center
- [ ] App status is "Allowed"

---

## 13. Testing & Verification

### Test OAuth Connection in Azure Portal

1. Go to **Bot Services** → Your bot → **Configuration**
2. Click on your OAuth connection (TeamsSSO)
3. Click **Test Connection**
4. Should show "Success" with your user info

### Test Bot in Teams

1. Open Teams
2. Find your bot in Apps (or search for it)
3. Start a chat with the bot
4. Send any message
5. Should see sign-in prompt (first time)
6. After sign-in, should get responses from Genie

### Check Application Logs

```bash
# Stream logs in real-time
az webapp log tail --name your-app --resource-group your-rg
```

**Look for these success indicators:**
- `Token stored for user ...` - SSO token received
- `TOKEN EXCHANGE RECEIVED` - Invoke activity processed
- `Databricks token obtained` - OBO exchange succeeded

---

## 14. Troubleshooting

### View Application Logs

```bash
# Stream logs
az webapp log tail --name your-app --resource-group your-rg

# Download logs
az webapp log download --name your-app --resource-group your-rg
```

### Restart Application

```bash
az webapp restart --name your-app --resource-group your-rg
```

### Test Health Endpoint

```bash
curl https://your-app.azurewebsites.net/health
```

### Verify Azure AD Configuration

```bash
az ad app show --id $APP_ID --query "{
  appId: appId,
  identifierUris: identifierUris,
  scopes: api.oauth2PermissionScopes[].value,
  permissions: requiredResourceAccess
}" -o json
```

### Verify Bot Service Configuration

```bash
az bot show --name your-bot --resource-group your-rg --query "{
  endpoint: properties.endpoint,
  msaAppId: properties.msaAppId
}" -o json
```

---

## Quick Reference

### Three Values That MUST Match

```
┌─────────────────────────────────────────────────────────────────────┐
│ These three values MUST be IDENTICAL for SSO to work:               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│ 1. Azure AD → Expose an API → Application ID URI                    │
│    api://botid-90c542b7-b31f-4f95-8ab2-0faee6e916db                 │
│                                                                      │
│ 2. Azure Bot Service → OAuth Connection → Token Exchange URL        │
│    api://botid-90c542b7-b31f-4f95-8ab2-0faee6e916db                 │
│                                                                      │
│ 3. manifest.json → webApplicationInfo.resource                      │
│    api://botid-90c542b7-b31f-4f95-8ab2-0faee6e916db                 │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### Required Redirect URI

```
https://token.botframework.com/.auth/web/redirect
```

### Teams Client IDs for Pre-Authorization

| Application | Client ID |
|-------------|-----------|
| Teams Desktop/Mobile | `1fec8e78-bce4-4aaf-ab1b-5451cc387264` |
| Teams Web | `5e3ce6c0-2b1f-4285-8d4b-75ee78787346` |

### Azure Databricks Resource ID

```
2ff814a6-3304-4ab8-85cb-cd0e6f879c1d
```

### Databricks user_impersonation Scope ID

```
b8d88131-27e4-4e92-a55f-dc714bd18b45
```

---

## 15. Security Best Practices

### Token Security

| Practice | Why | How |
|----------|-----|-----|
| **Never log tokens** | Tokens are credentials | Mask or omit tokens in logs |
| **Short TTL caching** | Limits exposure window | Cache tokens for ~1 hour max |
| **Encrypted storage** | Protect at rest | Use Key Vault, not environment variables |
| **Memory-only in app** | Avoid disk persistence | Store tokens in memory, not files |

### Consent & Permissions

| Practice | Why | How |
|----------|-----|-----|
| **Least privilege** | Minimize attack surface | Request only necessary scopes |
| **Admin consent** | Control access | Use admin consent for sensitive permissions |
| **Review permissions** | Audit access | Periodically review app permissions |

### User Identity Benefits

With user identity flow, you get:

1. **Audit Trails**: All Databricks queries are attributed to the actual user, not a service account
2. **Per-User Permissions**: Unity Catalog permissions apply - users only see data they're authorized to access
3. **Compliance**: Supports data governance requirements that mandate user-level access control
4. **Accountability**: Clear attribution of who accessed what data and when

### Token Lifetimes

| Token Type | Default Lifetime | Notes |
|------------|------------------|-------|
| Teams SSO Token | ~1 hour | Automatically refreshed by Teams |
| Databricks Token (OBO) | ~1 hour | Must re-exchange when expired |
| Refresh Tokens | Days/weeks | Used to get new access tokens |

### Rotation & Expiry

- **Client Secrets**: Rotate annually (set calendar reminder)
- **Monitor expiry**: Azure AD shows secret expiration dates
- **Plan for rotation**: Have a process to update secrets before expiry

### Network Security (Optional Enhancements)

For production environments, consider:

- **Private endpoints** for App Service
- **VNet integration** for Key Vault access
- **IP restrictions** on App Service
- **Azure Front Door** for DDoS protection

---

*Document Version: 1.1 | Last Updated: January 2026*
