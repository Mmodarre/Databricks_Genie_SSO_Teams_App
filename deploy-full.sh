#!/bin/bash
# =============================================================================
# Databricks Genie Bot - Full Azure Deployment Script
# =============================================================================
# This script deploys ALL Azure resources needed for the Genie Bot from scratch:
#   - Azure AD App Registration (with SSO configuration)
#   - Azure Key Vault
#   - Azure App Service
#   - Azure Bot Service (with Teams channel and OAuth connection)
#   - Teams App Package
#
# Prerequisites:
#   - Azure CLI installed and logged in (az login)
#   - jq installed (for JSON processing)
#   - .env file created from env.example with your values
#
# Usage:
#   chmod +x deploy-full.sh
#   ./deploy-full.sh
#
# For more details, see AZURE_DEPLOYMENT_GUIDE.md
# =============================================================================

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# -----------------------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------------------

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_phase() {
    echo ""
    echo -e "${GREEN}=============================================="
    echo -e "Phase $1: $2"
    echo -e "==============================================${NC}"
    echo ""
}

# Generate a random suffix for unique names
generate_suffix() {
    echo $(cat /dev/urandom | LC_ALL=C tr -dc 'a-z0-9' | fold -w 6 | head -n 1)
}

# -----------------------------------------------------------------------------
# Phase 1: Prerequisites & Validation
# -----------------------------------------------------------------------------

phase1_prerequisites() {
    log_phase "1" "Prerequisites & Validation"
    
    # Check Azure CLI
    if ! command -v az &> /dev/null; then
        log_error "Azure CLI is not installed. Please install it: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli"
        exit 1
    fi
    log_success "Azure CLI is installed"
    
    # Check if logged in
    if ! az account show &> /dev/null; then
        log_error "Not logged into Azure CLI. Please run: az login"
        exit 1
    fi
    CURRENT_USER=$(az account show --query "user.name" -o tsv)
    log_success "Logged in as: $CURRENT_USER"
    
    # Check jq
    if ! command -v jq &> /dev/null; then
        log_error "jq is not installed. Please install it:"
        log_error "  macOS: brew install jq"
        log_error "  Ubuntu: sudo apt-get install jq"
        exit 1
    fi
    log_success "jq is installed"
    
    # Check zip
    if ! command -v zip &> /dev/null; then
        log_error "zip is not installed. Please install it."
        exit 1
    fi
    log_success "zip is installed"
    
    # Check uuidgen
    if ! command -v uuidgen &> /dev/null; then
        log_error "uuidgen is not installed. Please install it:"
        log_error "  macOS: Already included"
        log_error "  Ubuntu: sudo apt-get install uuid-runtime"
        exit 1
    fi
    log_success "uuidgen is installed"
    
    # Load .env file
    if [ -f .env ]; then
        log_info "Loading configuration from .env file..."
        set -a
        source .env
        set +a
    else
        log_error ".env file not found!"
        log_error "Please copy env.example to .env and fill in your values."
        exit 1
    fi
    
    # Validate required variables
    if [ -z "$TENANT_ID" ]; then
        log_error "TENANT_ID is required in .env"
        exit 1
    fi
    
    if [ -z "$DATABRICKS_HOST" ]; then
        log_error "DATABRICKS_HOST is required in .env"
        exit 1
    fi
    
    if [ -z "$GENIE_SPACE_ID" ]; then
        log_error "GENIE_SPACE_ID is required in .env"
        exit 1
    fi
    
    log_success "Required configuration validated"
    
    # Generate names if not provided
    SUFFIX=$(generate_suffix)
    
    if [ -z "$BOT_NAME" ]; then
        BOT_NAME="genie-bot-$SUFFIX"
        log_info "Generated BOT_NAME: $BOT_NAME"
    fi
    
    if [ -z "$RESOURCE_GROUP" ]; then
        RESOURCE_GROUP="${BOT_NAME}-rg"
        log_info "Generated RESOURCE_GROUP: $RESOURCE_GROUP"
    fi
    
    if [ -z "$KEY_VAULT_NAME" ]; then
        KEY_VAULT_NAME="${BOT_NAME}-kv"
        log_info "Generated KEY_VAULT_NAME: $KEY_VAULT_NAME"
    fi
    
    if [ -z "$APP_SERVICE_PLAN" ]; then
        APP_SERVICE_PLAN="${BOT_NAME}-plan"
        log_info "Generated APP_SERVICE_PLAN: $APP_SERVICE_PLAN"
    fi
    
    if [ -z "$APP_NAME" ]; then
        APP_NAME="${BOT_NAME}-app"
        log_info "Generated APP_NAME: $APP_NAME"
    fi
    
    if [ -z "$LOCATION" ]; then
        LOCATION="eastus"
        log_info "Using default LOCATION: $LOCATION"
    fi
    
    # Derived values
    KEY_VAULT_URL="https://${KEY_VAULT_NAME}.vault.azure.net/"
    BOT_PUBLIC_URL="https://${APP_NAME}.azurewebsites.net"
    BOT_ENDPOINT="${BOT_PUBLIC_URL}/api/messages"
    
    echo ""
    log_info "Deployment Configuration:"
    echo "  Tenant ID:       $TENANT_ID"
    echo "  Location:        $LOCATION"
    echo "  Resource Group:  $RESOURCE_GROUP"
    echo "  Bot Name:        $BOT_NAME"
    echo "  Key Vault:       $KEY_VAULT_NAME"
    echo "  App Service:     $APP_NAME"
    echo "  Bot Endpoint:    $BOT_ENDPOINT"
    echo "  Databricks Host: $DATABRICKS_HOST"
    echo "  Genie Space ID:  $GENIE_SPACE_ID"
    echo ""
}

# -----------------------------------------------------------------------------
# Phase 2: Azure AD App Registration
# -----------------------------------------------------------------------------

phase2_azure_ad() {
    log_phase "2" "Azure AD App Registration"
    
    # 2.1 Create App Registration
    log_info "Creating App Registration: $BOT_NAME..."
    
    APP_CREATE_RESULT=$(az ad app create \
        --display-name "$BOT_NAME" \
        --sign-in-audience "AzureADMyOrg" \
        --output json)
    
    APP_ID=$(echo "$APP_CREATE_RESULT" | jq -r '.appId')
    APP_OBJECT_ID=$(echo "$APP_CREATE_RESULT" | jq -r '.id')
    
    if [ -z "$APP_ID" ] || [ "$APP_ID" == "null" ]; then
        log_error "Failed to create App Registration"
        exit 1
    fi
    
    log_success "App Registration created: $APP_ID"
    log_info "App Object ID: $APP_OBJECT_ID"
    
    # 2.2 Set Application ID URI (MUST be api://botid-{APP_ID} for Teams SSO)
    log_info "Setting Application ID URI..."
    APP_ID_URI="api://botid-$APP_ID"
    
    az ad app update \
        --id "$APP_ID" \
        --identifier-uris "$APP_ID_URI" \
        --output none
    
    log_success "Application ID URI set: $APP_ID_URI"
    
    # 2.3 Add access_as_user scope
    log_info "Adding access_as_user scope..."
    
    # Generate a UUID for the scope
    SCOPE_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
    
    az ad app update --id "$APP_ID" --set api="{
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
    }" --output none
    
    log_success "access_as_user scope added (ID: $SCOPE_ID)"
    
    # 2.4 Pre-authorize Teams client applications via Graph API
    log_info "Pre-authorizing Teams client applications..."
    
    # Teams Desktop/Mobile: 1fec8e78-bce4-4aaf-ab1b-5451cc387264
    # Teams Web: 5e3ce6c0-2b1f-4285-8d4b-75ee78787346
    
    az rest --method PATCH \
        --uri "https://graph.microsoft.com/v1.0/applications/$APP_OBJECT_ID" \
        --headers "Content-Type=application/json" \
        --body "{
            \"api\": {
                \"preAuthorizedApplications\": [
                    {
                        \"appId\": \"1fec8e78-bce4-4aaf-ab1b-5451cc387264\",
                        \"delegatedPermissionIds\": [\"$SCOPE_ID\"]
                    },
                    {
                        \"appId\": \"5e3ce6c0-2b1f-4285-8d4b-75ee78787346\",
                        \"delegatedPermissionIds\": [\"$SCOPE_ID\"]
                    }
                ]
            }
        }" --output none
    
    log_success "Teams clients pre-authorized"
    
    # 2.5 Add Databricks API permission
    log_info "Adding Databricks API permission..."
    
    DATABRICKS_RESOURCE_ID="2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"
    DATABRICKS_SCOPE_ID="b8d88131-27e4-4e92-a55f-dc714bd18b45"
    
    az ad app permission add \
        --id "$APP_ID" \
        --api "$DATABRICKS_RESOURCE_ID" \
        --api-permissions "$DATABRICKS_SCOPE_ID=Scope" \
        --output none
    
    log_success "Databricks API permission added"
    
    # Grant admin consent
    log_info "Granting admin consent for Databricks permission..."
    
    # First create the service principal
    az ad sp create --id "$APP_ID" --output none 2>/dev/null || true
    
    # Wait a moment for propagation
    sleep 5
    
    # Grant consent
    az ad app permission grant \
        --id "$APP_ID" \
        --api "$DATABRICKS_RESOURCE_ID" \
        --scope "user_impersonation" \
        --output none 2>/dev/null || log_warning "Admin consent may need to be granted manually in Azure Portal"
    
    log_success "Admin consent granted (or pending manual approval)"
    
    # 2.6 Add redirect URI
    log_info "Adding redirect URI..."
    
    az ad app update \
        --id "$APP_ID" \
        --web-redirect-uris "https://token.botframework.com/.auth/web/redirect" \
        --output none
    
    log_success "Redirect URI added"
    
    # 2.7 Create client secret
    log_info "Creating client secret..."
    
    SECRET_RESULT=$(az ad app credential reset \
        --id "$APP_ID" \
        --display-name "bot-secret" \
        --years 1 \
        --output json)
    
    APP_PASSWORD=$(echo "$SECRET_RESULT" | jq -r '.password')
    
    if [ -z "$APP_PASSWORD" ] || [ "$APP_PASSWORD" == "null" ]; then
        log_error "Failed to create client secret"
        exit 1
    fi
    
    log_success "Client secret created"
    
    # Export for later phases
    export APP_ID
    export APP_OBJECT_ID
    export APP_PASSWORD
    export APP_ID_URI
    export SCOPE_ID
}

# -----------------------------------------------------------------------------
# Phase 3: Azure Infrastructure
# -----------------------------------------------------------------------------

phase3_infrastructure() {
    log_phase "3" "Azure Infrastructure"
    
    # 3.1 Create Resource Group
    log_info "Creating Resource Group: $RESOURCE_GROUP..."
    
    az group create \
        --name "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        --output none
    
    log_success "Resource Group created"
    
    # 3.2 Create Key Vault
    log_info "Creating Key Vault: $KEY_VAULT_NAME..."
    
    az keyvault create \
        --name "$KEY_VAULT_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        --sku standard \
        --output none
    
    log_success "Key Vault created"
    
    # 3.3 Store secrets in Key Vault
    log_info "Storing secrets in Key Vault..."
    
    az keyvault secret set --vault-name "$KEY_VAULT_NAME" \
        --name "microsoft-app-id" \
        --value "$APP_ID" \
        --output none
    
    az keyvault secret set --vault-name "$KEY_VAULT_NAME" \
        --name "microsoft-app-password" \
        --value "$APP_PASSWORD" \
        --output none
    
    az keyvault secret set --vault-name "$KEY_VAULT_NAME" \
        --name "microsoft-app-tenant-id" \
        --value "$TENANT_ID" \
        --output none
    
    az keyvault secret set --vault-name "$KEY_VAULT_NAME" \
        --name "databricks-host" \
        --value "$DATABRICKS_HOST" \
        --output none
    
    az keyvault secret set --vault-name "$KEY_VAULT_NAME" \
        --name "genie-space-id" \
        --value "$GENIE_SPACE_ID" \
        --output none
    
    # Optional Databricks OAuth credentials
    if [ -n "$DATABRICKS_CLIENT_ID" ]; then
        az keyvault secret set --vault-name "$KEY_VAULT_NAME" \
            --name "databricks-client-id" \
            --value "$DATABRICKS_CLIENT_ID" \
            --output none
    fi
    
    if [ -n "$DATABRICKS_CLIENT_SECRET" ]; then
        az keyvault secret set --vault-name "$KEY_VAULT_NAME" \
            --name "databricks-client-secret" \
            --value "$DATABRICKS_CLIENT_SECRET" \
            --output none
    fi
    
    log_success "Secrets stored in Key Vault"
    
    # 3.4 Create App Service Plan
    log_info "Creating App Service Plan: $APP_SERVICE_PLAN..."
    
    az appservice plan create \
        --name "$APP_SERVICE_PLAN" \
        --resource-group "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        --is-linux \
        --sku B1 \
        --output none
    
    log_success "App Service Plan created"
    
    # 3.5 Create Web App
    log_info "Creating Web App: $APP_NAME..."
    
    az webapp create \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --plan "$APP_SERVICE_PLAN" \
        --runtime "PYTHON:3.12" \
        --output none
    
    log_success "Web App created"
    
    # 3.6 Configure App Settings
    log_info "Configuring app settings..."
    
    az webapp config appsettings set \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --settings \
            KEY_VAULT_URL="$KEY_VAULT_URL" \
            BOT_PUBLIC_URL="$BOT_PUBLIC_URL" \
            WEBSITES_PORT="8000" \
            SCM_DO_BUILD_DURING_DEPLOYMENT="true" \
        --output none
    
    log_success "App settings configured"
    
    # 3.7 Configure Startup Command
    log_info "Configuring startup command..."
    
    az webapp config set \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --startup-file "gunicorn --bind 0.0.0.0:8000 --timeout 600 --worker-class aiohttp.GunicornWebWorker app_azure:init_app" \
        --output none
    
    log_success "Startup command configured"
    
    # 3.8 Enable Managed Identity
    log_info "Enabling managed identity..."
    
    IDENTITY_PRINCIPAL_ID=$(az webapp identity assign \
        --name "$APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --query "principalId" \
        --output tsv)
    
    log_success "Managed identity enabled: $IDENTITY_PRINCIPAL_ID"
    
    # 3.9 Grant Key Vault Access
    log_info "Granting Key Vault access to managed identity..."
    
    az keyvault set-policy \
        --name "$KEY_VAULT_NAME" \
        --object-id "$IDENTITY_PRINCIPAL_ID" \
        --secret-permissions get list \
        --output none
    
    log_success "Key Vault access granted"
}

# -----------------------------------------------------------------------------
# Phase 4: Azure Bot Service
# -----------------------------------------------------------------------------

phase4_bot_service() {
    log_phase "4" "Azure Bot Service"
    
    # 4.1 Create Azure Bot
    log_info "Creating Azure Bot: $BOT_NAME..."
    
    az bot create \
        --resource-group "$RESOURCE_GROUP" \
        --name "$BOT_NAME" \
        --kind "azurebot" \
        --app-type "SingleTenant" \
        --appid "$APP_ID" \
        --tenant-id "$TENANT_ID" \
        --endpoint "$BOT_ENDPOINT" \
        --output none
    
    log_success "Azure Bot created"
    
    # 4.2 Enable Teams Channel
    log_info "Enabling Teams channel..."
    
    az bot msteams create \
        --name "$BOT_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --output none 2>/dev/null || log_warning "Teams channel may already exist or need manual setup"
    
    log_success "Teams channel enabled"
    
    # 4.3 Create OAuth Connection (TeamsSSO)
    log_info "Creating OAuth connection (TeamsSSO)..."
    
    # Note: az bot authsetting create has specific parameter requirements
    az bot authsetting create \
        --resource-group "$RESOURCE_GROUP" \
        --name "$BOT_NAME" \
        --setting-name "TeamsSSO" \
        --client-id "$APP_ID" \
        --client-secret "$APP_PASSWORD" \
        --service "Aadv2" \
        --provider-scope-string "openid profile User.Read" \
        --parameters tenantId="$TENANT_ID" tokenExchangeUrl="$APP_ID_URI" \
        --output none 2>/dev/null || log_warning "OAuth connection may need manual configuration in Azure Portal"
    
    log_success "OAuth connection created (TeamsSSO)"
}

# -----------------------------------------------------------------------------
# Phase 5: Teams Manifest Generation
# -----------------------------------------------------------------------------

phase5_manifest() {
    log_phase "5" "Teams Manifest Generation"
    
    log_info "Generating manifest.json from template..."
    
    # Check if template exists
    if [ ! -f "manifest.json.template" ]; then
        log_error "manifest.json.template not found!"
        exit 1
    fi
    
    # Default developer info if not set
    DEVELOPER_NAME="${DEVELOPER_NAME:-Your Company}"
    DEVELOPER_WEBSITE="${DEVELOPER_WEBSITE:-https://example.com}"
    DEVELOPER_PRIVACY="${DEVELOPER_PRIVACY:-https://example.com/privacy}"
    DEVELOPER_TERMS="${DEVELOPER_TERMS:-https://example.com/terms}"
    
    # Generate manifest.json from template
    sed -e "s/{{APP_ID}}/$APP_ID/g" \
        -e "s/{{APP_NAME}}/$APP_NAME/g" \
        -e "s/{{DEVELOPER_NAME}}/$DEVELOPER_NAME/g" \
        -e "s/{{DEVELOPER_WEBSITE}}/$DEVELOPER_WEBSITE/g" \
        -e "s/{{DEVELOPER_PRIVACY}}/$DEVELOPER_PRIVACY/g" \
        -e "s/{{DEVELOPER_TERMS}}/$DEVELOPER_TERMS/g" \
        manifest.json.template > manifest.json
    
    log_success "manifest.json generated"
    
    # Validate the manifest has correct values
    MANIFEST_APP_ID=$(jq -r '.id' manifest.json)
    MANIFEST_RESOURCE=$(jq -r '.webApplicationInfo.resource' manifest.json)
    
    log_info "Manifest validation:"
    echo "  App ID: $MANIFEST_APP_ID"
    echo "  Resource: $MANIFEST_RESOURCE"
    
    if [ "$MANIFEST_APP_ID" != "$APP_ID" ]; then
        log_error "Manifest App ID mismatch!"
        exit 1
    fi
    
    EXPECTED_RESOURCE="api://botid-$APP_ID"
    if [ "$MANIFEST_RESOURCE" != "$EXPECTED_RESOURCE" ]; then
        log_error "Manifest resource URI mismatch!"
        log_error "Expected: $EXPECTED_RESOURCE"
        log_error "Got: $MANIFEST_RESOURCE"
        exit 1
    fi
    
    log_success "Manifest validated"
    
    # Create Teams app package
    log_info "Creating Teams app package..."
    
    # Check for icons
    if [ ! -f "icon-color.png" ] || [ ! -f "icon-outline.png" ]; then
        log_warning "Icon files not found. Teams app package may be incomplete."
    fi
    
    rm -f teams-app.zip
    zip -j teams-app.zip manifest.json icon-color.png icon-outline.png 2>/dev/null || \
    zip -j teams-app.zip manifest.json 2>/dev/null
    
    log_success "Teams app package created: teams-app.zip"
}

# -----------------------------------------------------------------------------
# Phase 6: Code Deployment
# -----------------------------------------------------------------------------

phase6_deployment() {
    log_phase "6" "Code Deployment"
    
    # Check for required files
    if [ ! -f "app_azure.py" ]; then
        log_error "app_azure.py not found!"
        exit 1
    fi
    
    if [ ! -f "requirements.txt" ]; then
        log_error "requirements.txt not found!"
        exit 1
    fi
    
    # Create deployment package
    log_info "Creating deployment package..."
    
    rm -f deploy_package.zip
    zip -r deploy_package.zip \
        app_azure.py \
        requirements.txt \
        startup.sh \
        icon-color.png \
        icon-outline.png \
        -x "*.pyc" -x "__pycache__/*" -x ".env*" -x "*.zip" 2>/dev/null || \
    zip -r deploy_package.zip \
        app_azure.py \
        requirements.txt \
        -x "*.pyc" -x "__pycache__/*" -x ".env*" -x "*.zip"
    
    log_success "Deployment package created"
    
    # Deploy to App Service
    log_info "Deploying to App Service (this may take a few minutes)..."
    
    az webapp deploy \
        --resource-group "$RESOURCE_GROUP" \
        --name "$APP_NAME" \
        --src-path deploy_package.zip \
        --type zip \
        --output none
    
    log_success "Application deployed"
    
    # Clean up
    rm -f deploy_package.zip
    
    # Wait for deployment to complete
    log_info "Waiting for deployment to complete..."
    sleep 30
    
    # Health check
    log_info "Checking health endpoint..."
    
    HEALTH_URL="${BOT_PUBLIC_URL}/health"
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$HEALTH_URL" 2>/dev/null || echo "000")
    
    if [ "$HTTP_CODE" == "200" ]; then
        log_success "Health check passed!"
    else
        log_warning "Health check returned $HTTP_CODE (may still be starting up)"
        log_info "You can check manually: curl $HEALTH_URL"
    fi
}

# -----------------------------------------------------------------------------
# Phase 7: Summary & Next Steps
# -----------------------------------------------------------------------------

phase7_summary() {
    log_phase "7" "Deployment Complete!"
    
    # Save generated configuration
    cat > .env.generated << EOF
# =============================================================================
# Generated Configuration - $(date)
# =============================================================================
# IMPORTANT: Keep this file secure! It contains sensitive credentials.

# Azure AD App Registration
MICROSOFT_APP_ID=$APP_ID
MICROSOFT_APP_PASSWORD=$APP_PASSWORD
MICROSOFT_APP_TENANT_ID=$TENANT_ID
APP_ID_URI=$APP_ID_URI

# Resource Names
RESOURCE_GROUP=$RESOURCE_GROUP
BOT_NAME=$BOT_NAME
KEY_VAULT_NAME=$KEY_VAULT_NAME
KEY_VAULT_URL=$KEY_VAULT_URL
APP_SERVICE_PLAN=$APP_SERVICE_PLAN
APP_NAME=$APP_NAME
BOT_PUBLIC_URL=$BOT_PUBLIC_URL
BOT_ENDPOINT=$BOT_ENDPOINT

# Databricks
DATABRICKS_HOST=$DATABRICKS_HOST
GENIE_SPACE_ID=$GENIE_SPACE_ID
EOF

    log_success "Configuration saved to .env.generated"
    
    echo ""
    echo -e "${GREEN}=============================================="
    echo "DEPLOYMENT SUMMARY"
    echo "==============================================${NC}"
    echo ""
    echo "Azure AD App Registration:"
    echo "  App ID:              $APP_ID"
    echo "  Application ID URI:  $APP_ID_URI"
    echo ""
    echo "Azure Resources:"
    echo "  Resource Group:      $RESOURCE_GROUP"
    echo "  Key Vault:           $KEY_VAULT_NAME"
    echo "  App Service:         $APP_NAME"
    echo "  Bot Service:         $BOT_NAME"
    echo ""
    echo "Endpoints:"
    echo "  Bot Endpoint:        $BOT_ENDPOINT"
    echo "  Health Check:        ${BOT_PUBLIC_URL}/health"
    echo ""
    echo -e "${YELLOW}=============================================="
    echo "MANUAL STEPS REQUIRED"
    echo "==============================================${NC}"
    echo ""
    echo "1. Upload Teams App to Admin Center:"
    echo "   - Go to: https://admin.teams.microsoft.com"
    echo "   - Navigate to: Teams apps > Manage apps"
    echo "   - Click: Upload new app"
    echo "   - Select: teams-app.zip"
    echo ""
    echo "2. Configure Databricks (see DATABRICKS_SETUP.md):"
    echo "   - Ensure users exist in Databricks workspace"
    echo "   - Grant users access to Genie Space: $GENIE_SPACE_ID"
    echo "   - Verify Unity Catalog permissions"
    echo ""
    echo "3. Test the Bot:"
    echo "   - Open Microsoft Teams"
    echo "   - Search for 'Databricks Genie' in Apps"
    echo "   - Start a chat and send a message"
    echo ""
    echo -e "${GREEN}=============================================="
    echo "For detailed instructions, see:"
    echo "  - AZURE_DEPLOYMENT_GUIDE.md"
    echo "  - DATABRICKS_SETUP.md"
    echo "==============================================${NC}"
}

# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------

main() {
    echo ""
    echo -e "${GREEN}=============================================="
    echo "Databricks Genie Bot - Full Azure Deployment"
    echo "==============================================${NC}"
    echo ""
    
    phase1_prerequisites
    phase2_azure_ad
    phase3_infrastructure
    phase4_bot_service
    phase5_manifest
    phase6_deployment
    phase7_summary
}

# Run main function
main "$@"
