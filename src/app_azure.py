"""
Databricks Genie Bot for Microsoft Teams - Azure App Service Version.

A bot that connects to Databricks Genie to answer natural language questions about your data.
This version uses:
- Azure Key Vault for secure credential storage
- Teams SSO for user identity authentication
- OBO (On-Behalf-Of) flow to exchange tokens for Databricks access
- Azure App Service for hosting

Users MUST authenticate via Teams SSO - no fallback to Service Principal for queries.
Supports visualization of query results using charts when Genie provides [VIZ_START] blocks.
"""

import os
import sys
import re
import io
import base64
import traceback
import logging
import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from aiohttp import web
from dotenv import load_dotenv

# Load environment variables from .env file (for local development)
load_dotenv()

# Azure Key Vault SDK
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# MSAL for OBO token exchange
import msal

# Chart generation
import matplotlib
matplotlib.use('Agg')  # Non-GUI backend for server-side rendering
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

from aiohttp.web import Request, Response
from botbuilder.core import (
    BotFrameworkAdapterSettings,
    BotFrameworkAdapter,
    TurnContext,
    MemoryStorage,
)
# TeamsSSOTokenExchangeMiddleware removed - requires Azure Bot OAuth connection
from botbuilder.schema import Activity, ActivityTypes, Attachment, InvokeResponse

# Databricks SDK
from databricks.sdk import WorkspaceClient

# =============================================================================
# Configuration from Azure Key Vault
# =============================================================================

# Key Vault URL (must be set via environment variable or .env file)
KEY_VAULT_URL = os.getenv("KEY_VAULT_URL", "")

# Initialize credentials
logger.info(f"Connecting to Key Vault: {KEY_VAULT_URL}")
try:
    credential = DefaultAzureCredential()
    secret_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
    
    # Retrieve secrets from Key Vault
    APP_ID = secret_client.get_secret("microsoft-app-id").value
    APP_PASSWORD = secret_client.get_secret("microsoft-app-password").value
    APP_TENANT_ID = secret_client.get_secret("microsoft-app-tenant-id").value
    DATABRICKS_HOST = secret_client.get_secret("databricks-host").value
    DATABRICKS_CLIENT_ID = secret_client.get_secret("databricks-client-id").value
    DATABRICKS_CLIENT_SECRET = secret_client.get_secret("databricks-client-secret").value
    GENIE_SPACE_ID = secret_client.get_secret("genie-space-id").value
    
    logger.info("Successfully retrieved all secrets from Key Vault")
except Exception as e:
    logger.error(f"Failed to retrieve secrets from Key Vault: {e}")
    # Fallback to environment variables for local testing
    APP_ID = os.getenv("MICROSOFT_APP_ID", "")
    APP_PASSWORD = os.getenv("MICROSOFT_APP_PASSWORD", "")
    APP_TENANT_ID = os.getenv("MICROSOFT_APP_TENANT_ID", "")
    DATABRICKS_HOST = os.getenv("DATABRICKS_HOST", "")
    DATABRICKS_CLIENT_ID = os.getenv("DATABRICKS_CLIENT_ID", "")
    DATABRICKS_CLIENT_SECRET = os.getenv("DATABRICKS_CLIENT_SECRET", "")
    GENIE_SPACE_ID = os.getenv("GENIE_SPACE_ID", "")

# Port configuration - Azure App Service uses PORT env var
PORT = int(os.getenv("PORT", os.getenv("WEBSITES_PORT", "8000")))

# Bot public URL - Azure App Service URL (must be set via environment variable or .env file)
BOT_PUBLIC_URL = os.getenv("BOT_PUBLIC_URL", "")

# Single-tenant OAuth endpoint
OAUTH_ENDPOINT = f"https://login.microsoftonline.com/{APP_TENANT_ID}" if APP_TENANT_ID else ""

# Create adapter settings
SETTINGS = BotFrameworkAdapterSettings(
    app_id=APP_ID,
    app_password=APP_PASSWORD,
    channel_auth_tenant=APP_TENANT_ID,
    oauth_endpoint=OAUTH_ENDPOINT
)
ADAPTER = BotFrameworkAdapter(SETTINGS)

# =============================================================================
# SSO Configuration
# =============================================================================

# SSO Connection name (must match Azure Bot Service OAuth connection)
SSO_CONNECTION_NAME = "TeamsSSO"

# Azure Databricks Resource ID (fixed value for Azure Databricks)
DATABRICKS_RESOURCE_ID = "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d"

# SSO middleware removed - requires Azure Bot OAuth connection to be configured
# Will use simple sign-in card flow instead
STORAGE = MemoryStorage()

logger.info("Using simple OAuth flow (no SSO middleware)")


# =============================================================================
# User Token Manager - OBO Token Exchange
# =============================================================================

class UserTokenManager:
    """Manages user token acquisition and OBO exchange for Databricks."""
    
    def __init__(self, app_id: str, app_password: str, tenant_id: str):
        """
        Initialize the token manager.
        
        Args:
            app_id: Azure AD App ID
            app_password: Azure AD App Password/Secret
            tenant_id: Azure AD Tenant ID
        """
        self.app_id = app_id
        self.tenant_id = tenant_id
        self.msal_app = msal.ConfidentialClientApplication(
            client_id=app_id,
            client_credential=app_password,
            authority=f"https://login.microsoftonline.com/{tenant_id}"
        )
        # Cache: user_id -> {token, expires_at}
        self.user_tokens: Dict[str, Dict[str, Any]] = {}
        logger.info("UserTokenManager initialized")
    
    def exchange_for_databricks_token(self, user_id: str, user_token: str) -> Optional[str]:
        """
        Exchange user token for Databricks-scoped token via OBO flow.
        
        Args:
            user_id: User identifier for caching
            user_token: User's access token from Teams SSO
            
        Returns:
            Databricks-scoped access token, or None if exchange fails
        """
        # Check cache first
        cached = self.user_tokens.get(user_id)
        if cached and cached.get('expires_at', 0) > datetime.now().timestamp():
            logger.info(f"Using cached Databricks token for user {user_id}")
            return cached.get('token')
        
        try:
            # OBO exchange for Databricks scope
            result = self.msal_app.acquire_token_on_behalf_of(
                user_assertion=user_token,
                scopes=[f"{DATABRICKS_RESOURCE_ID}/user_impersonation"]
            )
            
            if "access_token" in result:
                token = result["access_token"]
                expires_in = result.get("expires_in", 3600)
                
                # Cache the token
                self.user_tokens[user_id] = {
                    'token': token,
                    'expires_at': datetime.now().timestamp() + expires_in - 60  # 60s buffer
                }
                
                logger.info(f"OBO exchange successful for user {user_id}, expires in {expires_in}s")
                return token
            else:
                error = result.get('error_description', result.get('error', 'Unknown error'))
                logger.error(f"OBO exchange failed for user {user_id}: {error}")
                return None
                
        except Exception as e:
            logger.error(f"OBO exchange error for user {user_id}: {e}")
            logger.error(traceback.format_exc())
            return None
    
    def clear_user_token(self, user_id: str):
        """Clear cached token for a user."""
        if user_id in self.user_tokens:
            del self.user_tokens[user_id]
            logger.info(f"Cleared cached token for user {user_id}")


# Initialize token manager
TOKEN_MANAGER = UserTokenManager(APP_ID, APP_PASSWORD, APP_TENANT_ID) if APP_ID and APP_PASSWORD else None


# =============================================================================
# Genie Client - Using User Token Authentication
# =============================================================================

class GenieClient:
    """Client for interacting with Databricks Genie API using user token."""
    
    def __init__(self, host: str, token: str, space_id: str):
        """
        Initialize the Genie client with user token authentication.
        
        Args:
            host: Databricks workspace URL
            token: User's Databricks-scoped access token
            space_id: Genie Space ID
        """
        self.space_id = space_id
        self.host = host
        # Use user token authentication
        self.client = WorkspaceClient(host=host, token=token)
        logger.info(f"GenieClient initialized for space: {space_id} (using user token auth)")
    
    def ask_question(self, question: str) -> Dict[str, Any]:
        """
        Start a new conversation with a question.
        
        Args:
            question: Natural language question
            
        Returns:
            Dictionary with text response, SQL, data, and conversation_id
        """
        # Append instruction to include visualization spec
        question_with_viz = f"{question}\n\n**IMPORTANT** Make sure to include [VIZ_START] visualization block in your response."
        logger.info(f"Asking Genie: {question}")
        
        try:
            # Start conversation and wait for completion
            response = self.client.genie.start_conversation_and_wait(
                space_id=self.space_id,
                content=question_with_viz,
                timeout=timedelta(minutes=5)
            )
            
            logger.info(f"Genie response received - conversation_id: {response.conversation_id}")
            return self._parse_response(response)
            
        except Exception as e:
            logger.error(f"Error asking Genie: {e}")
            logger.error(traceback.format_exc())
            return {
                "text": f"Sorry, I encountered an error: {str(e)}",
                "sql": None,
                "columns": [],
                "data_rows": [],
                "conversation_id": None,
                "error": True
            }
    
    def follow_up(self, conversation_id: str, question: str) -> Dict[str, Any]:
        """
        Send a follow-up question in an existing conversation.
        
        Args:
            conversation_id: Existing conversation ID
            question: Follow-up question
            
        Returns:
            Dictionary with text response, SQL, data, and conversation_id
        """
        question_with_viz = f"{question}\n\nMake sure to include [VIZ_START] visualization block in your response."
        logger.info(f"Follow-up in conversation {conversation_id}: {question}")
        
        try:
            response = self.client.genie.create_message_and_wait(
                space_id=self.space_id,
                conversation_id=conversation_id,
                content=question_with_viz,
                timeout=timedelta(minutes=5)
            )
            
            logger.info(f"Genie follow-up response received")
            result = self._parse_response(response)
            result["conversation_id"] = conversation_id
            return result
            
        except Exception as e:
            logger.error(f"Error in follow-up: {e}")
            logger.error(traceback.format_exc())
            return {
                "text": f"Sorry, I encountered an error: {str(e)}",
                "sql": None,
                "columns": [],
                "data_rows": [],
                "conversation_id": conversation_id,
                "error": True
            }
    
    def _parse_response(self, genie_message) -> Dict[str, Any]:
        """
        Parse GenieMessage to extract text, SQL, and data.
        
        Args:
            genie_message: GenieMessage object from SDK
            
        Returns:
            Dictionary with parsed response data
        """
        result = {
            "text": "",
            "sql": None,
            "columns": [],
            "data_rows": [],
            "conversation_id": getattr(genie_message, 'conversation_id', None),
            "suggested_questions": [],
            "error": False
        }
        
        # Check for error status
        if hasattr(genie_message, 'status') and genie_message.status in ['FAILED', 'CANCELLED']:
            error_msg = getattr(genie_message, 'error', None)
            result["text"] = f"Query failed: {error_msg}" if error_msg else "Query was cancelled or failed."
            result["error"] = True
            return result
        
        # Collect all text parts from attachments
        text_parts = []
        
        # Process attachments
        if hasattr(genie_message, 'attachments') and genie_message.attachments:
            for attachment in genie_message.attachments:
                # Extract text responses
                if hasattr(attachment, 'text') and attachment.text:
                    if hasattr(attachment.text, 'content') and attachment.text.content:
                        text_parts.append(attachment.text.content)
                
                # Extract SQL query
                if hasattr(attachment, 'query') and attachment.query:
                    if hasattr(attachment.query, 'query'):
                        result["sql"] = attachment.query.query
                    if hasattr(attachment.query, 'description') and attachment.query.description:
                        desc = attachment.query.description
                        if desc and desc not in text_parts:
                            text_parts.insert(0, desc)
                
                # Extract suggested follow-up questions
                if hasattr(attachment, 'suggested_questions') and attachment.suggested_questions:
                    sq = attachment.suggested_questions
                    if hasattr(sq, 'questions') and sq.questions:
                        result["suggested_questions"] = list(sq.questions)
                        logger.info(f"Found {len(result['suggested_questions'])} suggested questions")
        
        # Combine all text parts
        if text_parts:
            result["text"] = "\n\n".join(text_parts)
        
        # Get query results from the message's query_result attribute
        if hasattr(genie_message, 'query_result') and genie_message.query_result:
            qr = genie_message.query_result
            logger.info(f"Found query_result on message with {getattr(qr, 'row_count', 0)} rows")
            
            if hasattr(qr, 'statement_response') and qr.statement_response:
                stmt_resp = qr.statement_response
                if hasattr(stmt_resp, 'manifest') and stmt_resp.manifest:
                    if hasattr(stmt_resp.manifest, 'schema') and stmt_resp.manifest.schema:
                        if hasattr(stmt_resp.manifest.schema, 'columns'):
                            result["columns"] = [
                                col.name for col in stmt_resp.manifest.schema.columns
                            ]
                if hasattr(stmt_resp, 'result') and stmt_resp.result:
                    if hasattr(stmt_resp.result, 'data_array'):
                        result["data_rows"] = stmt_resp.result.data_array or []
                        logger.info(f"Extracted {len(result['data_rows'])} data rows")
        
        # If no results yet, try fetching via API using attachment_id
        if not result["data_rows"] and hasattr(genie_message, 'attachments') and genie_message.attachments:
            for attachment in genie_message.attachments:
                att_id = getattr(attachment, 'attachment_id', None)
                if att_id and result["conversation_id"]:
                    try:
                        query_result = self.client.genie.get_message_attachment_query_result(
                            space_id=self.space_id,
                            conversation_id=result["conversation_id"],
                            message_id=genie_message.id,
                            attachment_id=att_id
                        )
                        
                        if hasattr(query_result, 'statement_response') and query_result.statement_response:
                            stmt_resp = query_result.statement_response
                            if hasattr(stmt_resp, 'manifest') and stmt_resp.manifest:
                                if hasattr(stmt_resp.manifest, 'schema') and stmt_resp.manifest.schema:
                                    if hasattr(stmt_resp.manifest.schema, 'columns'):
                                        result["columns"] = [
                                            col.name for col in stmt_resp.manifest.schema.columns
                                        ]
                            if hasattr(stmt_resp, 'result') and stmt_resp.result:
                                if hasattr(stmt_resp.result, 'data_array') and stmt_resp.result.data_array:
                                    result["data_rows"] = stmt_resp.result.data_array
                                    logger.info(f"Fetched {len(result['data_rows'])} rows via attachment API")
                                    break
                                    
                    except Exception as e:
                        logger.warning(f"Could not fetch query result for attachment {att_id}: {e}")
        
        # Default text if none found
        if not result["text"]:
            if result["sql"]:
                result["text"] = "Here's what I found:"
            else:
                result["text"] = "I processed your question but didn't find a specific answer."
        
        return result


# =============================================================================
# Visualization Spec Parser
# =============================================================================

def parse_viz_spec(text: str) -> Tuple[Optional[Dict[str, str]], str]:
    """
    Extract visualization specification from Genie response text.
    
    Parses the [VIZ_START]...[VIZ_END] block if present.
    
    Args:
        text: The raw text response from Genie
        
    Returns:
        Tuple of (viz_spec dict or None, cleaned text without VIZ block)
    """
    if not text:
        return None, text
    
    pattern = r'\[VIZ_START\](.*?)\[VIZ_END\]'
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    
    if not match:
        return None, text
    
    viz_block = match.group(1).strip()
    spec: Dict[str, str] = {}
    
    known_fields = ['chart_type', 'x_label', 'y_label', 'x_axis', 'y_axis', 'title', 'sort']
    
    if '\n' in viz_block:
        for line in viz_block.split('\n'):
            line = line.strip()
            if ':' in line:
                key, value = line.split(':', 1)
                spec[key.strip().lower()] = value.strip()
    else:
        for field in known_fields:
            other_fields = [f for f in known_fields if f != field]
            lookahead = '|'.join(other_fields)
            field_pattern = rf'{field}\s*:\s*(.+?)(?=\s+(?:{lookahead})\s*:|$)'
            field_match = re.search(field_pattern, viz_block, re.IGNORECASE)
            if field_match:
                spec[field] = field_match.group(1).strip()
    
    required_fields = ['chart_type', 'x_axis', 'y_axis']
    if not all(field in spec for field in required_fields):
        logger.warning(f"VIZ spec missing required fields. Found: {list(spec.keys())}")
        return None, text
    
    cleaned_text = re.sub(pattern, '', text, flags=re.DOTALL | re.IGNORECASE).strip()
    
    logger.info(f"Parsed VIZ spec: chart_type={spec.get('chart_type')}, "
                f"x_axis={spec.get('x_axis')}, y_axis={spec.get('y_axis')}")
    
    return spec, cleaned_text


# =============================================================================
# Chart Generator
# =============================================================================

CHART_COLORS: List[str] = [
    '#077A9D', '#FFAB00', '#00A972', '#FF3621', '#8BCAE7',
    '#AB4057', '#99DDB4', '#FCA4A1', '#919191', '#BF7080',
]


class ChartGenerator:
    """Generates chart images from query data using matplotlib."""
    
    def __init__(self, colors: Optional[List[str]] = None):
        self.colors = colors or CHART_COLORS
        self.figure_size = (8, 5)
        self.dpi = 80
    
    def generate(
        self,
        viz_spec: Dict[str, str],
        columns: List[str],
        data_rows: List[List[Any]]
    ) -> Optional[str]:
        """Generate a chart image as a base64-encoded PNG string."""
        try:
            chart_type = viz_spec.get('chart_type', 'bar').lower()
            x_col = viz_spec.get('x_axis', '')
            y_col = viz_spec.get('y_axis', '')
            
            if ',' in y_col:
                y_col = y_col.split(',')[0].strip()
            
            title = viz_spec.get('title', 'Chart')
            x_label = viz_spec.get('x_label', x_col)
            y_label = viz_spec.get('y_label', y_col)
            sort_order = viz_spec.get('sort', 'none').lower()
            
            x_idx = self._get_column_index(columns, x_col)
            y_idx = self._get_column_index(columns, y_col)
            
            if x_idx is None or y_idx is None:
                logger.error(f"Column not found. x_col={x_col}, y_col={y_col}, columns={columns}")
                return None
            
            x_data = [row[x_idx] for row in data_rows if row[x_idx] is not None]
            y_data = [self._to_numeric(row[y_idx]) for row in data_rows if row[x_idx] is not None]
            
            if not x_data or not y_data:
                logger.warning("No valid data for chart generation")
                return None
            
            if sort_order in ['asc', 'desc']:
                paired = list(zip(x_data, y_data))
                paired.sort(key=lambda x: x[1], reverse=(sort_order == 'desc'))
                x_data, y_data = zip(*paired) if paired else ([], [])
                x_data, y_data = list(x_data), list(y_data)
            
            chart_methods = {
                'bar': self._create_bar_chart,
                'line': self._create_line_chart,
                'pie': self._create_pie_chart,
                'scatter': self._create_scatter_chart,
                'area': self._create_area_chart,
            }
            
            chart_method = chart_methods.get(chart_type, self._create_bar_chart)
            
            return chart_method(
                x_data=x_data,
                y_data=y_data,
                title=title,
                x_label=x_label,
                y_label=y_label
            )
            
        except Exception as e:
            logger.error(f"Error generating chart: {e}")
            logger.error(traceback.format_exc())
            return None
    
    def _get_column_index(self, columns: List[str], col_name: str) -> Optional[int]:
        col_name_lower = col_name.lower()
        for i, col in enumerate(columns):
            if col.lower() == col_name_lower:
                return i
        return None
    
    def _to_numeric(self, value: Any) -> float:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            return float(value)
        try:
            cleaned = re.sub(r'[$,€£%]', '', str(value))
            return float(cleaned)
        except (ValueError, TypeError):
            return 0.0
    
    def _finalize_chart(self, fig: plt.Figure) -> str:
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=self.dpi, bbox_inches='tight',
                    facecolor='white', edgecolor='none')
        buf.seek(0)
        img_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        plt.close(fig)
        return img_base64
    
    def _create_bar_chart(self, x_data, y_data, title, x_label, y_label) -> str:
        fig, ax = plt.subplots(figsize=self.figure_size)
        
        max_bars = 15
        if len(x_data) > max_bars:
            x_data = x_data[:max_bars]
            y_data = y_data[:max_bars]
        
        colors = [self.colors[i % len(self.colors)] for i in range(len(x_data))]
        y_pos = range(len(x_data))
        ax.barh(y_pos, y_data, color=colors, edgecolor='white', linewidth=0.5)
        
        ax.set_yticks(y_pos)
        ax.set_yticklabels([str(x)[:30] for x in x_data])
        ax.invert_yaxis()
        ax.set_xlabel(y_label)
        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        
        for i, (val, bar) in enumerate(zip(y_data, ax.patches)):
            ax.text(bar.get_width() + max(y_data) * 0.01, bar.get_y() + bar.get_height() / 2,
                    self._format_number(val), va='center', fontsize=9)
        
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: self._format_number(x)))
        
        fig.tight_layout()
        return self._finalize_chart(fig)
    
    def _create_line_chart(self, x_data, y_data, title, x_label, y_label) -> str:
        fig, ax = plt.subplots(figsize=self.figure_size)
        
        ax.plot(range(len(x_data)), y_data, color=self.colors[0], linewidth=2,
                marker='o', markersize=6, markerfacecolor='white', markeredgewidth=2)
        ax.fill_between(range(len(x_data)), y_data, alpha=0.1, color=self.colors[0])
        
        ax.set_xticks(range(len(x_data)))
        ax.set_xticklabels([str(x)[:15] for x in x_data], rotation=45, ha='right')
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: self._format_number(x)))
        ax.grid(axis='y', alpha=0.3)
        
        fig.tight_layout()
        return self._finalize_chart(fig)
    
    def _create_pie_chart(self, x_data, y_data, title, x_label, y_label) -> str:
        fig, ax = plt.subplots(figsize=(8, 8))
        
        max_slices = 8
        if len(x_data) > max_slices:
            paired = list(zip(x_data, y_data))
            paired.sort(key=lambda x: x[1], reverse=True)
            top_items = paired[:max_slices - 1]
            other_sum = sum(item[1] for item in paired[max_slices - 1:])
            x_data = [item[0] for item in top_items] + ['Other']
            y_data = [item[1] for item in top_items] + [other_sum]
        
        colors = [self.colors[i % len(self.colors)] for i in range(len(x_data))]
        
        wedges, texts, autotexts = ax.pie(
            y_data, labels=None,
            autopct=lambda pct: f'{pct:.1f}%' if pct > 3 else '',
            colors=colors, startangle=90,
            wedgeprops={'edgecolor': 'white', 'linewidth': 2}
        )
        
        for autotext in autotexts:
            autotext.set_fontsize(10)
            autotext.set_fontweight('bold')
        
        ax.legend(wedges,
            [f'{str(x)[:20]} ({self._format_number(y)})' for x, y in zip(x_data, y_data)],
            loc='center left', bbox_to_anchor=(1, 0.5), fontsize=9
        )
        
        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        fig.tight_layout()
        return self._finalize_chart(fig)
    
    def _create_scatter_chart(self, x_data, y_data, title, x_label, y_label) -> str:
        fig, ax = plt.subplots(figsize=self.figure_size)
        
        x_numeric = [self._to_numeric(x) for x in x_data]
        ax.scatter(x_numeric, y_data, c=self.colors[0], s=80, alpha=0.7,
                   edgecolors='white', linewidth=1)
        
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(alpha=0.3)
        
        fig.tight_layout()
        return self._finalize_chart(fig)
    
    def _create_area_chart(self, x_data, y_data, title, x_label, y_label) -> str:
        fig, ax = plt.subplots(figsize=self.figure_size)
        
        ax.fill_between(range(len(x_data)), y_data, alpha=0.4, color=self.colors[0])
        ax.plot(range(len(x_data)), y_data, color=self.colors[0], linewidth=2)
        
        ax.set_xticks(range(len(x_data)))
        ax.set_xticklabels([str(x)[:15] for x in x_data], rotation=45, ha='right')
        ax.set_xlabel(x_label)
        ax.set_ylabel(y_label)
        ax.set_title(title, fontsize=14, fontweight='bold', pad=15)
        
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, _: self._format_number(x)))
        ax.grid(axis='y', alpha=0.3)
        
        fig.tight_layout()
        return self._finalize_chart(fig)
    
    def _format_number(self, value: float) -> str:
        if value >= 1_000_000:
            return f'{value / 1_000_000:.1f}M'
        elif value >= 1_000:
            return f'{value / 1_000:.1f}K'
        elif value >= 1:
            return f'{value:.0f}'
        else:
            return f'{value:.2f}'


# Create global chart generator
chart_generator = ChartGenerator()


# =============================================================================
# Adaptive Card Helpers
# =============================================================================

PAGE_SIZE = 20


def create_paginated_card(
    result: Dict[str, Any], 
    page: int = 0, 
    page_size: int = PAGE_SIZE,
    result_id: str = None
) -> Dict[str, Any]:
    """Create an Adaptive Card with paginated table data."""
    columns = result.get("columns", [])
    all_rows = result.get("data_rows", [])
    total_rows = len(all_rows)
    total_pages = max(1, (total_rows + page_size - 1) // page_size)
    
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * page_size
    end_idx = min(start_idx + page_size, total_rows)
    page_rows = all_rows[start_idx:end_idx]
    
    body = []
    
    if total_rows > 0:
        body.append({
            "type": "TextBlock",
            "text": f"Results: Page {page + 1} of {total_pages} ({total_rows} total rows)",
            "weight": "Bolder",
            "size": "Medium"
        })
    
    if columns and page_rows:
        table_columns = [{"width": 1} for _ in columns]
        
        header_cells = [{
            "type": "TableCell",
            "items": [{"type": "TextBlock", "text": str(col), "weight": "Bolder", "wrap": True}]
        } for col in columns]
        
        table_rows = [{"type": "TableRow", "cells": header_cells, "style": "accent"}]
        
        for row in page_rows:
            row_cells = []
            for val in row:
                display_val = "-" if val is None else str(val)[:50] + ("..." if len(str(val)) > 50 else "")
                row_cells.append({
                    "type": "TableCell",
                    "items": [{"type": "TextBlock", "text": display_val, "wrap": True}]
                })
            table_rows.append({"type": "TableRow", "cells": row_cells})
        
        body.append({
            "type": "Table",
            "columns": table_columns,
            "rows": table_rows,
            "gridStyle": "accent",
            "firstRowAsHeader": True,
            "showGridLines": True,
            "spacing": "Medium"
        })
    
    actions = []
    if page > 0:
        actions.append({
            "type": "Action.Submit",
            "title": "< Previous",
            "data": {"action": "pagination", "direction": "prev", "page": page - 1, "result_id": result_id}
        })
    if page < total_pages - 1:
        actions.append({
            "type": "Action.Submit",
            "title": "Next >",
            "data": {"action": "pagination", "direction": "next", "page": page + 1, "result_id": result_id}
        })
    
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": body,
        "msteams": {"width": "Full"}
    }
    
    if actions:
        card["actions"] = actions
    
    return card


def create_card_attachment(card: Dict[str, Any]) -> Attachment:
    return Attachment(content_type="application/vnd.microsoft.card.adaptive", content=card)


def create_chart_card(
    chart_image_base64: str,
    title: str,
    result_id: str,
    chart_type: str = "bar",
    interactive_url: Optional[str] = None
) -> Dict[str, Any]:
    """Create an Adaptive Card displaying a chart image."""
    image_url = f"data:image/png;base64,{chart_image_base64}"
    
    actions = [{
        "type": "Action.Submit",
        "title": "View Data Table",
        "data": {"action": "view_data", "result_id": result_id}
    }]
    
    if interactive_url:
        actions.insert(0, {
            "type": "Action.OpenUrl",
            "title": "Open Interactive Chart",
            "url": interactive_url
        })
    
    return {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.5",
        "body": [
            {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Large", "wrap": True},
            {"type": "Image", "url": image_url, "size": "Stretch", "altText": f"{chart_type.title()} chart: {title}"}
        ],
        "actions": actions,
        "msteams": {"width": "Full"}
    }


# =============================================================================
# Error Handler
# =============================================================================

async def on_error(context: TurnContext, error: Exception):
    logger.error(f"Unhandled error: {error}")
    logger.error(traceback.format_exc())
    await context.send_activity("Oops! Something went wrong. Please try again.")

ADAPTER.on_turn_error = on_error


# =============================================================================
# Genie Bot - Main Bot Class
# =============================================================================

class GenieBot:
    """A bot that answers questions using Databricks Genie with user identity (SSO required)."""
    
    def __init__(self):
        # User session data
        self.user_conversations: Dict[str, str] = {}  # user_id -> genie_conversation_id
        self.user_preferences: Dict[str, Dict[str, Any]] = {}
        self.user_tokens: Dict[str, str] = {}  # user_id -> teams_token (for SSO)
        
        # Query results cache
        self.query_results: Dict[str, Dict[str, Any]] = {}
        self._result_counter = 0
        
        # Chart images cache
        self.chart_images: Dict[str, str] = {}
        self._chart_counter = 0
        
        logger.info("GenieBot initialized (SSO-only mode - no Service Principal for queries)")
    
    def _get_user_pref(self, user_id: str, key: str, default: Any = None) -> Any:
        return self.user_preferences.get(user_id, {}).get(key, default)
    
    def _set_user_pref(self, user_id: str, key: str, value: Any):
        if user_id not in self.user_preferences:
            self.user_preferences[user_id] = {}
        self.user_preferences[user_id][key] = value
    
    def _store_result(self, result: Dict[str, Any]) -> str:
        self._result_counter += 1
        result_id = f"result_{self._result_counter}"
        self.query_results[result_id] = result
        
        if len(self.query_results) > 50:
            oldest_keys = list(self.query_results.keys())[:-50]
            for key in oldest_keys:
                del self.query_results[key]
        
        return result_id
    
    def store_chart(self, chart_base64: str) -> str:
        self._chart_counter += 1
        chart_id = f"chart_{self._chart_counter}"
        self.chart_images[chart_id] = chart_base64
        
        if len(self.chart_images) > 100:
            oldest_keys = list(self.chart_images.keys())[:-100]
            for key in oldest_keys:
                del self.chart_images[key]
        
        return chart_id
    
    def get_chart(self, chart_id: str) -> Optional[str]:
        return self.chart_images.get(chart_id)
    
    # =========================================================================
    # SSO Token Handling Methods
    # =========================================================================
    
    async def _get_user_token(self, turn_context: TurnContext) -> Optional[str]:
        """
        Get the user's Teams SSO token from the Bot Framework token service.
        
        Args:
            turn_context: The turn context
            
        Returns:
            User's access token, or None if not authenticated
        """
        user_id = turn_context.activity.from_property.id if turn_context.activity.from_property else None
        
        if not user_id:
            return None
        
        # Check if we have a cached token
        if user_id in self.user_tokens:
            return self.user_tokens[user_id]
        
        # Try to get token from Bot Framework token service
        try:
            from botbuilder.core import UserTokenClient
            
            # Get the token client from the turn context
            token_client = turn_context.turn_state.get("ConnectorClient")
            if token_client:
                user_token_client = UserTokenClient(token_client.config.credentials)
                token_response = await user_token_client.get_user_token(
                    user_id=user_id,
                    connection_name=SSO_CONNECTION_NAME,
                    channel_id=turn_context.activity.channel_id
                )
                if token_response and token_response.token:
                    self.user_tokens[user_id] = token_response.token
                    return token_response.token
        except Exception as e:
            logger.debug(f"Could not get token from UserTokenClient: {e}")
        
        return None
    
    async def _exchange_for_databricks_token(self, user_id: str, user_token: str) -> Optional[str]:
        """
        Exchange user's Teams token for a Databricks-scoped token.
        
        Args:
            user_id: User identifier
            user_token: User's Teams SSO token
            
        Returns:
            Databricks access token, or None if exchange fails
        """
        if not TOKEN_MANAGER:
            logger.error("TokenManager not initialized - cannot exchange tokens")
            return None
        
        return TOKEN_MANAGER.exchange_for_databricks_token(user_id, user_token)
    
    def _create_signin_card(self) -> Dict[str, Any]:
        """Create an Adaptive Card prompting user to sign in."""
        return {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.5",
            "body": [
                {
                    "type": "TextBlock",
                    "text": "Authentication Required",
                    "weight": "Bolder",
                    "size": "Large"
                },
                {
                    "type": "TextBlock",
                    "text": "Please sign in to access Databricks Genie and query your data.",
                    "wrap": True
                },
                {
                    "type": "TextBlock",
                    "text": "Your queries will run with your own identity and permissions.",
                    "wrap": True,
                    "size": "Small",
                    "isSubtle": True
                }
            ],
            "actions": [
                {
                    "type": "Action.Submit",
                    "title": "Sign In",
                    "data": {"action": "signin"}
                }
            ],
            "msteams": {"width": "Full"}
        }
    
    async def _send_signin_card(self, turn_context: TurnContext):
        """Send a sign-in card to the user."""
        card = self._create_signin_card()
        attachment = create_card_attachment(card)
        await turn_context.send_activity(
            Activity(type=ActivityTypes.message, attachments=[attachment])
        )
        logger.info("Sign-in card sent")
    
    async def _trigger_sso_signin(self, turn_context: TurnContext):
        """Trigger the SSO sign-in flow by sending an OAuthCard."""
        from botbuilder.schema import OAuthCard, CardAction
        
        logger.info(f"Sending OAuthCard for connection: {SSO_CONNECTION_NAME}")
        
        oauth_card = OAuthCard(
            text="Please sign in to continue",
            connection_name=SSO_CONNECTION_NAME,
            token_exchange_resource={
                "id": SSO_CONNECTION_NAME,
                "uri": f"api://botid-{APP_ID}"
            },
            buttons=[
                CardAction(
                    type="signin",
                    title="Sign In",
                    value=SSO_CONNECTION_NAME
                )
            ]
        )
        
        attachment = Attachment(
            content_type="application/vnd.microsoft.card.oauth",
            content=oauth_card
        )
        
        await turn_context.send_activity(
            Activity(type=ActivityTypes.message, attachments=[attachment])
        )
        logger.info("OAuthCard sent")
    
    def _create_genie_client(self, databricks_token: str) -> GenieClient:
        """Create a GenieClient with the user's Databricks token."""
        return GenieClient(
            host=DATABRICKS_HOST,
            token=databricks_token,
            space_id=GENIE_SPACE_ID
        )
    
    # =========================================================================
    # Main Turn Handler
    # =========================================================================
    
    async def on_turn(self, turn_context: TurnContext):
        activity = turn_context.activity
        sender_id = activity.from_property.id if activity.from_property else "Unknown"
        
        logger.info(f"Activity Type: {activity.type}, From: {sender_id}")
        
        # Log invoke details
        if activity.type == ActivityTypes.invoke:
            logger.info(f"Invoke Name: {activity.name}, Value: {activity.value}")
        
        if activity.type == ActivityTypes.invoke:
            await self._handle_invoke(turn_context)
            return
        
        if activity.type == ActivityTypes.message:
            if activity.value and not activity.text:
                await self._handle_card_action(turn_context, activity.value)
                return
            await self._handle_message(turn_context, sender_id, activity.text)
        
        elif activity.type == ActivityTypes.conversation_update:
            await self._handle_conversation_update(turn_context, activity)
    
    async def _handle_invoke(self, turn_context: TurnContext):
        activity = turn_context.activity
        value = activity.value or {}
        invoke_name = activity.name
        
        logger.info(f"Handling invoke: name={invoke_name}, value={value}")
        
        # Handle SSO token exchange
        if invoke_name == "signin/tokenExchange":
            await self._handle_token_exchange(turn_context, value)
            return
        
        if invoke_name == "task/fetch":
            await self._handle_task_fetch(turn_context, value)
            return
        
        if invoke_name == "task/submit":
            await turn_context.send_activity(
                Activity(type=ActivityTypes.invoke_response, value=InvokeResponse(status=200, body={}))
            )
            return
        
        action = value.get("action")
        
        if action == "pagination":
            await self._handle_pagination(turn_context, value)
        elif action == "view_data":
            await self._handle_view_data(turn_context, value)
        else:
            await turn_context.send_activity(
                Activity(type=ActivityTypes.invoke_response, value=InvokeResponse(status=200))
            )
    
    async def _handle_token_exchange(self, turn_context: TurnContext, value: Dict[str, Any]):
        """Handle the signin/tokenExchange invoke from Teams SSO."""
        user_id = turn_context.activity.from_property.id if turn_context.activity.from_property else None
        token = value.get("token")
        
        logger.info(f"=== TOKEN EXCHANGE RECEIVED ===")
        logger.info(f"User ID: {user_id}")
        logger.info(f"Token present: {bool(token)}")
        logger.info(f"Value keys: {list(value.keys())}")
        
        if token and user_id:
            # Store the token for this user
            self.user_tokens[user_id] = token
            logger.info(f"Token stored for user {user_id}")
            
            # Send success response - MUST use InvokeResponse object
            await turn_context.send_activity(
                Activity(
                    type=ActivityTypes.invoke_response,
                    value=InvokeResponse(status=200, body={"id": value.get("id")})
                )
            )
            
            # Send a confirmation message
            await turn_context.send_activity(
                "You're now signed in. Ask me anything about your data!"
            )
        else:
            logger.warning(f"Token exchange failed - no token received")
            await turn_context.send_activity(
                Activity(
                    type=ActivityTypes.invoke_response,
                    value=InvokeResponse(status=400, body={"error": "Token not provided"})
                )
            )
    
    async def _handle_card_action(self, turn_context: TurnContext, value: Dict[str, Any]):
        action = value.get("action")
        
        if action == "signin":
            # User clicked Sign In button - send OAuthCard to trigger SSO
            logger.info("User clicked signin action, sending OAuthCard")
            await self._trigger_sso_signin(turn_context)
            return
        elif action == "pagination":
            await self._handle_pagination(turn_context, value)
        elif action == "view_data":
            await self._handle_view_data(turn_context, value)
    
    async def _handle_pagination(self, turn_context: TurnContext, value: Dict[str, Any]):
        result_id = value.get("result_id")
        page = value.get("page", 0)
        
        if result_id and result_id in self.query_results:
            result = self.query_results[result_id]
            card = create_paginated_card(result, page=page, result_id=result_id)
            attachment = create_card_attachment(card)
            await turn_context.send_activity(Activity(type=ActivityTypes.message, attachments=[attachment]))
        else:
            await turn_context.send_activity("Sorry, I couldn't find the data. Please run the query again.")
    
    async def _handle_view_data(self, turn_context: TurnContext, value: Dict[str, Any]):
        result_id = value.get("result_id")
        
        if result_id and result_id in self.query_results:
            result = self.query_results[result_id]
            card = create_paginated_card(result, page=0, result_id=result_id)
            attachment = create_card_attachment(card)
            await turn_context.send_activity(Activity(type=ActivityTypes.message, attachments=[attachment]))
        else:
            await turn_context.send_activity("Sorry, I couldn't find the data. Please run the query again.")
    
    async def _handle_task_fetch(self, turn_context: TurnContext, value: Dict[str, Any]):
        data = value.get("data", value)
        result_id = data.get("result_id")
        url = data.get("url")
        title = data.get("title", "Interactive Chart")
        
        if not result_id or result_id not in self.query_results:
            response = {"task": {"type": "message", "value": "Chart data has expired. Please run the query again."}}
        else:
            response = {"task": {"type": "continue", "value": {"title": title, "height": 600, "width": 900, "url": url}}}
        
        await turn_context.send_activity(
            Activity(type=ActivityTypes.invoke_response, value=InvokeResponse(status=200, body=response))
        )
    
    async def _handle_message(self, turn_context: TurnContext, user_id: str, message: str):
        # =====================================================================
        # SSO Authentication Required
        # =====================================================================
        
        # Get user's Teams SSO token
        user_token = self.user_tokens.get(user_id)
        
        if not user_token:
            # No token - show sign-in card
            logger.info(f"No token for user {user_id}, sending sign-in card")
            await self._send_signin_card(turn_context)
            return
        
        # Exchange for Databricks token
        databricks_token = await self._exchange_for_databricks_token(user_id, user_token)
        
        if not databricks_token:
            logger.error(f"Failed to get Databricks token for user {user_id}")
            await turn_context.send_activity(
                "**Authentication Error**\n\n"
                "Failed to authenticate with Databricks. Please try signing in again."
            )
            # Clear the cached token and prompt re-auth
            self.user_tokens.pop(user_id, None)
            if TOKEN_MANAGER:
                TOKEN_MANAGER.clear_user_token(user_id)
            await self._send_signin_card(turn_context)
            return
        
        # =====================================================================
        # Handle Commands (these don't need Genie)
        # =====================================================================
        
        # Command: /new or /reset
        if message and message.lower().strip() in ["/new", "/reset", "new conversation", "start over"]:
            self.user_conversations.pop(user_id, None)
            await turn_context.send_activity("**New conversation started!**\n\nAsk me anything about your data.")
            return
        
        # Command: /sql on
        if message and message.lower().strip() == "/sql on":
            self._set_user_pref(user_id, "show_sql", True)
            await turn_context.send_activity("**SQL display is now ON.** Generated SQL queries will be shown.")
            return
        
        # Command: /sql off
        if message and message.lower().strip() == "/sql off":
            self._set_user_pref(user_id, "show_sql", False)
            await turn_context.send_activity("**SQL display is now OFF.** SQL queries will be hidden.")
            return
        
        # Command: /help
        if message and message.lower().strip() in ["/help", "help"]:
            show_sql = self._get_user_pref(user_id, "show_sql", True)
            sql_status = "ON" if show_sql else "OFF"
            await turn_context.send_activity(
                "**Databricks Genie Bot Help**\n\n"
                "I can answer questions about your data using natural language.\n\n"
                "**Commands:**\n"
                "- Just type your question to ask about your data\n"
                "- `/new` or `/reset` - Start a new conversation\n"
                "- `/sql on` - Show generated SQL queries\n"
                "- `/sql off` - Hide generated SQL queries\n"
                "- `/help` - Show this help message\n\n"
                f"**Current Settings:** SQL display is **{sql_status}**\n\n"
                "**Tips:**\n"
                "- Ask follow-up questions to refine your results\n"
                "- Be specific about time periods, metrics, and filters"
            )
            return
        
        # Command: /signout
        if message and message.lower().strip() in ["/signout", "/logout"]:
            self.user_tokens.pop(user_id, None)
            if TOKEN_MANAGER:
                TOKEN_MANAGER.clear_user_token(user_id)
            self.user_conversations.pop(user_id, None)
            await turn_context.send_activity("**Signed out successfully.**\n\nYou'll need to sign in again to continue.")
            return
        
        # =====================================================================
        # Process Query with User's Identity
        # =====================================================================
        
        # Send typing indicator
        await turn_context.send_activity(Activity(type=ActivityTypes.typing))
        
        # Create GenieClient with user's Databricks token
        genie_client = self._create_genie_client(databricks_token)
        
        genie_conversation_id = self.user_conversations.get(user_id)
        
        try:
            loop = asyncio.get_event_loop()
            
            if genie_conversation_id:
                logger.info(f"Follow-up for user {user_id} in conversation {genie_conversation_id}")
                result = await loop.run_in_executor(None, genie_client.follow_up, genie_conversation_id, message)
            else:
                logger.info(f"Starting new Genie conversation for user {user_id}")
                result = await loop.run_in_executor(None, genie_client.ask_question, message)
            
            if result.get("conversation_id") and not result.get("error"):
                self.user_conversations[user_id] = result["conversation_id"]
            
            # Parse visualization spec
            raw_text = result.get('text', '')
            viz_spec, clean_text = parse_viz_spec(raw_text)
            result['text'] = clean_text
            
            if viz_spec:
                logger.info(f"VIZ spec found: {viz_spec}")
            
            # Store result for pagination
            result_id = None
            if result.get("data_rows") and result.get("columns"):
                if viz_spec:
                    result['viz_spec'] = viz_spec
                result_id = self._store_result(result)
                logger.info(f"Stored result {result_id} with {len(result['data_rows'])} rows")
            
            # Send text response
            if clean_text or result.get("suggested_questions"):
                text_response = clean_text if clean_text else ""
                
                # Add suggested questions
                if result.get("suggested_questions"):
                    text_response += "\n\n---\n**Try asking:**"
                    for i, question in enumerate(result["suggested_questions"], 1):
                        text_response += f"\n{i}. {question}"
                
                # Add SQL if user wants it
                show_sql = self._get_user_pref(user_id, "show_sql", True)
                if show_sql and result.get("sql"):
                    text_response += f"\n\n---\n**Generated SQL:**\n```sql\n{result['sql']}\n```"
                
                if text_response.strip():
                    await turn_context.send_activity(Activity(type=ActivityTypes.message, text=text_response))
            
            # Generate and send chart
            chart_generated = False
            if viz_spec and result.get("data_rows") and result.get("columns") and result_id:
                logger.info(f"Generating chart: type={viz_spec.get('chart_type')}")
                
                chart_image = chart_generator.generate(
                    viz_spec=viz_spec,
                    columns=result["columns"],
                    data_rows=result["data_rows"]
                )
                
                if chart_image:
                    logger.info(f"Chart generated, base64 size: {len(chart_image)} chars")
                    
                    chart_title = viz_spec.get('title', 'Query Results')
                    chart_type = viz_spec.get('chart_type', 'bar')
                    interactive_url = f"{BOT_PUBLIC_URL}/interactive-chart/{result_id}"
                    
                    chart_card = create_chart_card(
                        chart_image_base64=chart_image,
                        title=chart_title,
                        result_id=result_id,
                        chart_type=chart_type,
                        interactive_url=interactive_url
                    )
                    attachment = create_card_attachment(chart_card)
                    
                    await turn_context.send_activity(Activity(type=ActivityTypes.message, attachments=[attachment]))
                    chart_generated = True
                else:
                    logger.warning("Chart generation failed")
                    await turn_context.send_activity("_Note: Could not generate chart. Showing data table instead._")
            
            # Show data table if no chart
            if not chart_generated and result.get("data_rows") and result.get("columns") and result_id:
                card = create_paginated_card(result, page=0, result_id=result_id)
                attachment = create_card_attachment(card)
                await turn_context.send_activity(Activity(type=ActivityTypes.message, attachments=[attachment]))
            elif not result.get("text") and not result.get("data_rows"):
                await turn_context.send_activity("I processed your question but didn't find any results.")
            
            logger.info("Response sent to user")
            
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            logger.error(traceback.format_exc())
            await turn_context.send_activity(f"**Error**\n\nSorry, I encountered an error: {str(e)}")
    
    async def _handle_conversation_update(self, turn_context: TurnContext, activity):
        if activity.members_added:
            for member in activity.members_added:
                if member.id != activity.recipient.id:
                    welcome_text = (
                        "**Welcome to Databricks Genie Bot!**\n\n"
                        "I can help you explore and analyze your data using natural language.\n\n"
                        "**Getting Started:**\n"
                        "1. Send any message to begin\n"
                        "2. You'll be asked to sign in (one-time)\n"
                        "3. Your queries will run with your own permissions\n\n"
                        "**Example questions:**\n"
                        "- \"What were our top 10 products last month?\"\n"
                        "- \"Show me sales by region\"\n"
                        "- \"Compare this quarter to last quarter\"\n\n"
                        "Type `/help` for more commands. Let's get started!"
                    )
                    await turn_context.send_activity(welcome_text)
                    logger.info(f"Welcome message sent to: {member.name if member.name else member.id}")


# =============================================================================
# Create Bot Instance
# =============================================================================

BOT = GenieBot()


# =============================================================================
# Web Server Endpoints
# =============================================================================

async def messages(req: Request) -> Response:
    """Main bot message endpoint."""
    if "application/json" in req.headers.get("Content-Type", ""):
        body = await req.json()
    else:
        return Response(status=415)

    activity = Activity().deserialize(body)
    auth_header = req.headers.get("Authorization", "")

    response = await ADAPTER.process_activity(activity, auth_header, BOT.on_turn)
    
    if response:
        # Handle response body - serialize if it's an object
        body = response.body
        if body is not None and not isinstance(body, (str, bytes)):
            import json
            try:
                # Try to serialize the object
                if hasattr(body, 'serialize'):
                    body = json.dumps(body.serialize())
                elif hasattr(body, '__dict__'):
                    body = json.dumps(body.__dict__)
                else:
                    body = str(body)
            except Exception as e:
                logger.warning(f"Could not serialize response body: {e}")
                body = None
        return Response(status=response.status, body=body)
    return Response(status=201)


async def health(req: Request) -> Response:
    """Health check endpoint."""
    return Response(
        status=200, 
        text=f"Bot running! SSO enabled: {TOKEN_MANAGER is not None}, Databricks host: {bool(DATABRICKS_HOST)}"
    )


# =============================================================================
# Interactive Chart Template (Plotly.js)
# =============================================================================

INTERACTIVE_CHART_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f5f5; padding: 16px; }}
        .container {{ background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); padding: 20px; }}
        h1 {{ font-size: 18px; color: #333; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid #eee; }}
        #chart {{ width: 100%; height: 450px; }}
        .controls {{ margin-top: 12px; padding-top: 12px; border-top: 1px solid #eee; display: flex; gap: 8px; flex-wrap: wrap; }}
        .controls button {{ padding: 8px 16px; border: 1px solid #ddd; border-radius: 4px; background: white; cursor: pointer; font-size: 13px; }}
        .controls button:hover {{ background: #f0f0f0; }}
        .controls button.active {{ background: #077A9D; color: white; border-color: #077A9D; }}
        .info {{ font-size: 12px; color: #666; margin-top: 8px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{title}</h1>
        <div id="chart"></div>
        <div class="controls">
            <button onclick="setChartType('bar')" id="btn-bar">Bar</button>
            <button onclick="setChartType('line')" id="btn-line">Line</button>
            <button onclick="setChartType('pie')" id="btn-pie">Pie</button>
            <button onclick="resetZoom()">Reset Zoom</button>
            <button onclick="downloadChart()">Download PNG</button>
        </div>
        <div class="info">Tip: Hover for values, drag to zoom, double-click to reset</div>
    </div>
    
    <script>
        const chartData = {chart_data_json};
        const colors = {colors_json};
        let currentType = '{chart_type}';
        
        function createChart(type) {{
            currentType = type;
            const x = chartData.x;
            const y = chartData.y;
            
            let trace, layout;
            
            if (type === 'pie') {{
                trace = {{ type: 'pie', labels: x, values: y, marker: {{ colors: colors }}, textinfo: 'label+percent', hovertemplate: '%{{label}}: %{{value:,.0f}}<extra></extra>' }};
                layout = {{ showlegend: true, legend: {{ orientation: 'h', y: -0.1 }} }};
            }} else {{
                trace = {{ x: x, y: y, type: type === 'line' ? 'scatter' : 'bar', mode: type === 'line' ? 'lines+markers' : undefined, marker: {{ color: colors[0] }}, line: type === 'line' ? {{ color: colors[0], width: 2 }} : undefined, hovertemplate: '%{{x}}: %{{y:,.0f}}<extra></extra>' }};
                layout = {{ xaxis: {{ title: '{x_label}', tickangle: -45 }}, yaxis: {{ title: '{y_label}' }}, bargap: 0.3 }};
            }}
            
            Plotly.newPlot('chart', [trace], {{ ...layout, margin: {{ t: 20, r: 20, b: 80, l: 60 }}, paper_bgcolor: 'white', plot_bgcolor: 'white' }}, {{ responsive: true, displayModeBar: true, modeBarButtonsToRemove: ['lasso2d', 'select2d'], displaylogo: false }});
            
            document.querySelectorAll('.controls button').forEach(btn => btn.classList.remove('active'));
            document.getElementById('btn-' + type)?.classList.add('active');
        }}
        
        function setChartType(type) {{ createChart(type); }}
        function resetZoom() {{ Plotly.relayout('chart', {{ 'xaxis.autorange': true, 'yaxis.autorange': true }}); }}
        function downloadChart() {{ Plotly.downloadImage('chart', {{ format: 'png', width: 1200, height: 800, filename: '{title}' }}); }}
        
        createChart(currentType);
    </script>
</body>
</html>
'''


async def serve_chart(req: Request) -> Response:
    """Serve chart images by ID."""
    chart_id = req.match_info.get('chart_id', '')
    
    if not chart_id:
        return Response(status=400, text="Missing chart_id")
    
    chart_base64 = BOT.get_chart(chart_id)
    
    if not chart_base64:
        return Response(status=404, text="Chart not found")
    
    try:
        chart_binary = base64.b64decode(chart_base64)
        return Response(status=200, body=chart_binary, content_type="image/png", headers={"Cache-Control": "public, max-age=3600"})
    except Exception as e:
        logger.error(f"Error serving chart {chart_id}: {e}")
        return Response(status=500, text="Error serving chart")


async def interactive_chart(req: Request) -> Response:
    """Serve interactive chart page using Plotly.js."""
    result_id = req.match_info.get('result_id', '')
    
    if not result_id:
        return Response(status=400, text="Missing result_id")
    
    result = BOT.query_results.get(result_id)
    
    if not result:
        return Response(status=404, text="<html><body><h1>Chart data not found</h1><p>The data may have expired. Please run the query again.</p></body></html>", content_type="text/html")
    
    viz_spec = result.get('viz_spec', {})
    columns = result.get('columns', [])
    data_rows = result.get('data_rows', [])
    
    if not columns or not data_rows:
        return Response(status=400, text="<html><body><h1>No data available</h1></body></html>", content_type="text/html")
    
    x_col = viz_spec.get('x_axis', columns[0])
    y_col = viz_spec.get('y_axis', columns[1] if len(columns) > 1 else columns[0])
    
    if ',' in y_col:
        y_col = y_col.split(',')[0].strip()
    
    x_idx = next((i for i, c in enumerate(columns) if c.lower() == x_col.lower()), 0)
    y_idx = next((i for i, c in enumerate(columns) if c.lower() == y_col.lower()), 1 if len(columns) > 1 else 0)
    
    x_data = [str(row[x_idx]) for row in data_rows if row[x_idx] is not None]
    y_data = []
    for row in data_rows:
        if row[x_idx] is not None:
            try:
                val = row[y_idx]
                if isinstance(val, (int, float)):
                    y_data.append(float(val))
                else:
                    cleaned = re.sub(r'[$,€£%]', '', str(val))
                    y_data.append(float(cleaned))
            except (ValueError, TypeError):
                y_data.append(0)
    
    chart_data = {'x': x_data, 'y': y_data}
    
    html = INTERACTIVE_CHART_TEMPLATE.format(
        title=viz_spec.get('title', 'Query Results'),
        chart_data_json=json.dumps(chart_data),
        colors_json=json.dumps(CHART_COLORS),
        chart_type=viz_spec.get('chart_type', 'bar'),
        x_label=viz_spec.get('x_label', x_col),
        y_label=viz_spec.get('y_label', y_col)
    )
    
    return Response(status=200, text=html, content_type="text/html")


def init_app():
    """Initialize the web application."""
    app = web.Application()
    app.router.add_post("/api/messages", messages)
    app.router.add_get("/health", health)
    app.router.add_get("/", health)
    app.router.add_get("/charts/{chart_id}", serve_chart)
    app.router.add_get("/interactive-chart/{result_id}", interactive_chart)
    return app


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    APP = init_app()
    
    print("=" * 60)
    print("Databricks Genie Bot Starting (Azure App Service)...")
    print("=" * 60)
    print(f"Port: {PORT}")
    print(f"Bot endpoint: /api/messages")
    print(f"Health check: /health")
    print(f"Databricks Host: {DATABRICKS_HOST}")
    print(f"Genie Space ID: {GENIE_SPACE_ID}")
    print(f"Service Principal configured: {'Yes' if DATABRICKS_CLIENT_ID else 'NO'}")
    print(f"Bot Public URL: {BOT_PUBLIC_URL}")
    print("=" * 60)
    
    web.run_app(APP, host="0.0.0.0", port=PORT)
