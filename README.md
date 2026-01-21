# Databricks Genie Bot for Microsoft Teams

A Microsoft Teams bot that enables users to query their Databricks data using natural language through [Databricks Genie](https://docs.databricks.com/en/genie/index.html). Features **Single Sign-On (SSO)** with user identity flow - queries run with each user's own permissions for proper data governance.

![Databricks Genie Bot in Action](image.png)

*Ask questions in natural language, get SQL queries, data tables, and auto-generated visualizations.*

## Features

- **Natural Language Queries** - Ask questions about your data in plain English
- **User Identity Flow** - Queries run with the user's Databricks permissions (not a shared service account)
- **SSO Authentication** - Seamless sign-in through Microsoft Teams
- **Auto-Generated Visualizations** - Charts automatically created based on your query results
- **Interactive Charts** - Bar, line, pie, scatter, and area charts with zoom and download options
- **Data Tables** - Paginated results with easy navigation
- **Follow-up Questions** - Continue conversations with context
- **SQL Transparency** - View the generated SQL queries
- **Smart Suggestions** - "Try asking" prompts for follow-up questions
- **Secure by Design** - Credentials stored in Azure Key Vault


## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Microsoft  │────▶│  Azure Bot  │────▶│   Azure     │────▶│  Databricks │
│    Teams    │     │   Service   │     │ App Service │     │    Genie    │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
                           │                   │
                           │              ┌────┴────┐
                           │              │  Azure  │
                           └─────────────▶│Key Vault│
                                          └─────────┘
```

**Authentication Flow:**
1. User sends message in Teams
2. Bot triggers SSO - Teams provides user token silently
3. Bot exchanges token for Databricks-scoped token (OBO flow)
4. Genie API called with user's identity
5. Results returned with user's data permissions applied

## Quick Start

### Prerequisites

- Azure subscription with Owner/Contributor access
- Azure AD admin access (to create app registrations)
- Databricks workspace with Genie enabled
- Microsoft 365 account with Teams
- Azure CLI installed (`az login` authenticated)

### One-Command Deployment

```bash
# 1. Clone the repository
git clone https://github.com/your-org/databricks-genie-teams-bot.git
cd databricks-genie-teams-bot

# 2. Copy and configure environment
cp env.example .env
# Edit .env with your values (TENANT_ID, DATABRICKS_HOST, GENIE_SPACE_ID)

# 3. Run full deployment
chmod +x deploy-full.sh
./deploy-full.sh
```

The script creates all Azure resources:
- Azure AD App Registration (with SSO configuration)
- Azure Key Vault (stores credentials securely)
- Azure App Service (hosts the bot)
- Azure Bot Service (with Teams channel)
- Teams app package (`teams-app.zip`)

### Manual Steps After Deployment

1. **Upload Teams App** (see [TEAMS_APP_UPLOAD.md](TEAMS_APP_UPLOAD.md) for detailed instructions)
   - **Sideload** for personal testing, or
   - **Upload to Org Catalog** via [Teams Admin Center](https://admin.teams.microsoft.com) for organization-wide access

2. **Configure Databricks** (see [DATABRICKS_SETUP.md](DATABRICKS_SETUP.md))
   - Ensure users exist in Databricks workspace
   - Grant users access to your Genie Space
   - Verify Unity Catalog permissions

3. **Test the Bot**
   - Open Microsoft Teams
   - Search for "Databricks Genie" in Apps
   - Start a chat and ask a question!

## Project Structure

```
├── app_azure.py              # Main bot application
├── requirements.txt          # Python dependencies
├── startup.sh                # Azure App Service startup script
├── deploy-full.sh            # Full automated deployment script
├── deploy.sh                 # Infrastructure-only deployment
├── env.example               # Environment template
├── manifest.json.template    # Teams manifest template
├── manifest.json.example     # Example manifest with placeholders
├── icon-color.png            # Teams app icon (color)
├── icon-outline.png          # Teams app icon (outline)
├── AZURE_DEPLOYMENT_GUIDE.md # Detailed Azure setup guide
├── DATABRICKS_SETUP.md       # Databricks configuration guide
└── README.md                 # This file
```

## Configuration

### Required Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `TENANT_ID` | Azure AD Tenant ID | `6c239608-a51e-...` |
| `DATABRICKS_HOST` | Databricks workspace URL | `https://adb-123.12.azuredatabricks.net` |
| `GENIE_SPACE_ID` | Genie Space ID | `01efabcd-1234-...` |

### Optional Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LOCATION` | Azure region | `eastus` |
| `BOT_NAME` | Base name for resources | Auto-generated |
| `DATABRICKS_CLIENT_ID` | Databricks OAuth app ID | (optional) |
| `DATABRICKS_CLIENT_SECRET` | Databricks OAuth secret | (optional) |

## Usage

Once installed, interact with the bot in Teams:

**Ask questions:**
```
What were our top 10 products last month?
Show me sales by region for Q4
Compare revenue this year vs last year
```

**Commands:**
- `/new` or `/reset` - Start a new conversation
- `/sql on` - Show generated SQL queries
- `/sql off` - Hide SQL queries
- `/signout` - Sign out and clear session
- `/help` - Show help message

## Documentation

- [AZURE_DEPLOYMENT_GUIDE.md](AZURE_DEPLOYMENT_GUIDE.md) - Complete Azure setup with SSO
- [DATABRICKS_SETUP.md](DATABRICKS_SETUP.md) - Databricks user and permission configuration
- [TEAMS_APP_UPLOAD.md](TEAMS_APP_UPLOAD.md) - How to upload/sideload the Teams app

## Security

This solution implements security best practices:

- **User Identity Flow** - Each user's queries run with their own permissions
- **Azure Key Vault** - All credentials stored securely, never in code
- **Managed Identity** - App Service authenticates to Key Vault without credentials
- **Single Tenant** - Restricted to your Azure AD organization
- **SSO** - No password entry required, leverages existing Teams authentication

## Requirements

- Python 3.11+
- Azure CLI 2.50+
- Databricks workspace with Genie enabled
- Unity Catalog configured (for data permissions)

## Troubleshooting

**Bot not responding:**
```bash
# Check health endpoint
curl https://your-bot-app.azurewebsites.net/health

# View logs
az webapp log tail --name your-bot-app --resource-group your-rg
```

**SSO not working:**
- Verify Application ID URI is `api://botid-{APP_ID}`
- Check Token Exchange URL in Bot Service matches
- Ensure Teams clients are pre-authorized

**Permission denied in Databricks:**
- Verify user exists in Databricks workspace
- Check Genie Space access permissions
- Verify Unity Catalog grants

See [AZURE_DEPLOYMENT_GUIDE.md](AZURE_DEPLOYMENT_GUIDE.md#14-troubleshooting) for more.

## Contributing

Contributions are welcome! Please read our contributing guidelines and submit pull requests.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- [Databricks Genie](https://docs.databricks.com/en/genie/index.html) - Natural language data querying
- [Microsoft Bot Framework](https://dev.botframework.com/) - Bot development platform
- [Azure Bot Service](https://azure.microsoft.com/services/bot-services/) - Bot hosting and channels
