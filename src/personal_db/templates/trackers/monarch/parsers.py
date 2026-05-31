"""Small read-only Monarch Money client used by the Monarch tracker.

This intentionally vendors only the calls personal_db needs instead of pulling
in the broad unofficial `monarchmoney` package. It stores only the Monarch auth
token in a local JSON file.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
import time
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api.monarch.com"


class MonarchMFARequired(RuntimeError):
    pass


class MonarchLoginError(RuntimeError):
    pass


class MonarchClient:
    def __init__(
        self,
        session_file: Path,
        timeout: int = 30,
        *,
        email: str | None = None,
        password: str | None = None,
        totp_secret: str | None = None,
    ) -> None:
        self.session_file = Path(session_file)
        self.timeout = timeout
        self.token: str | None = None
        self.email = email
        self.password = password
        self.totp_secret = totp_secret

    def _headers(self, *, include_auth: bool = True) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Client-Platform": "web",
            "Content-Type": "application/json",
            "User-Agent": "personal_db Monarch tracker",
        }
        if include_auth and self.token:
            headers["Authorization"] = f"Token {self.token}"
        return headers

    @property
    def headers(self) -> dict[str, str]:
        return self._headers()

    def configure_login(
        self,
        *,
        email: str | None = None,
        password: str | None = None,
        totp_secret: str | None = None,
    ) -> None:
        self.email = email or self.email
        self.password = password or self.password
        self.totp_secret = totp_secret or self.totp_secret

    def save_session(self) -> None:
        if not self.token:
            raise MonarchLoginError("Cannot save Monarch session without a token")
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.session_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"token": self.token}, indent=2, sort_keys=True))
        tmp.chmod(0o600)
        tmp.replace(self.session_file)
        self.session_file.chmod(0o600)

    def load_session(self) -> None:
        data = json.loads(self.session_file.read_text())
        token = data.get("token")
        if not token:
            raise MonarchLoginError(f"Malformed Monarch session at {self.session_file}")
        self.token = token

    def login(self, email: str, password: str) -> None:
        self._login(email=email, password=password, code=None)
        self.save_session()

    def mfa_login(self, email: str, password: str, code: str) -> None:
        self._login(email=email, password=password, code=code)
        self.save_session()

    def login_with_totp_secret(self, email: str, password: str, secret: str) -> None:
        self._login(email=email, password=password, code=generate_totp(secret))
        self.save_session()

    def _login(self, *, email: str, password: str, code: str | None) -> None:
        payload: dict[str, Any] = {
            "password": password,
            "supports_mfa": True,
            "trusted_device": False,
            "username": email,
        }
        if code:
            payload["totp"] = code
        response = requests.post(
            f"{BASE_URL}/auth/login/",
            headers=self._headers(include_auth=False),
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code == 403:
            raise MonarchMFARequired("Multi-factor authentication required")
        if response.status_code != 200:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            detail = body.get("detail") if isinstance(body, dict) else body
            error_code = body.get("error_code") if isinstance(body, dict) else None
            raise MonarchLoginError(
                f"Monarch login failed ({response.status_code}): {detail or error_code or response.reason}"
            )
        body = response.json()
        token = body.get("token")
        if not token:
            raise MonarchLoginError("Monarch login response did not include a token")
        self.token = token

    def refresh_session(self) -> None:
        if not self.email or not self.password:
            raise MonarchLoginError(
                "Monarch session expired and MONARCH_EMAIL/MONARCH_PASSWORD are not configured"
            )
        if self.totp_secret:
            self.login_with_totp_secret(
                email=self.email,
                password=self.password,
                secret=self.totp_secret,
            )
        else:
            self.login(email=self.email, password=self.password)

    def _post_graphql(
        self,
        operation: str,
        query: str,
        variables: dict[str, Any] | None,
    ) -> requests.Response:
        return requests.post(
            f"{BASE_URL}/graphql",
            headers=self.headers,
            json={
                "operationName": operation,
                "query": query,
                "variables": variables or {},
            },
            timeout=self.timeout,
        )

    def gql(self, operation: str, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.token:
            self.load_session()
        response = self._post_graphql(operation, query, variables)
        if response.status_code == 401:
            self.refresh_session()
            response = self._post_graphql(operation, query, variables)
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = response.text
            raise RuntimeError(f"Monarch GraphQL {operation} failed ({response.status_code}): {body}")
        body = response.json()
        if body.get("errors"):
            raise RuntimeError(f"Monarch GraphQL {operation} returned errors: {body['errors']}")
        return body.get("data") or {}

    def get_accounts(self) -> dict[str, Any]:
        return self.gql("GetAccounts", ACCOUNTS_QUERY)

    def get_transactions(
        self,
        *,
        limit: int,
        offset: int,
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        return self.gql(
            "GetTransactionsList",
            TRANSACTIONS_QUERY,
            {
                "offset": offset,
                "limit": limit,
                "orderBy": "date",
                "filters": {
                    "search": "",
                    "categories": [],
                    "accounts": [],
                    "tags": [],
                    "startDate": start_date,
                    "endDate": end_date,
                },
            },
        )

    def get_recent_account_balances(self, *, start_date: str) -> dict[str, Any]:
        return self.gql("GetAccountRecentBalances", RECENT_BALANCES_QUERY, {"startDate": start_date})

    def get_account_holdings(self, *, account_id: str, day: str) -> dict[str, Any]:
        return self.gql(
            "Web_GetHoldings",
            HOLDINGS_QUERY,
            {
                "input": {
                    "accountIds": [str(account_id)],
                    "endDate": day,
                    "includeHiddenHoldings": True,
                    "startDate": day,
                }
            },
        )


def generate_totp(secret: str, *, for_time: int | None = None, interval: int = 30, digits: int = 6) -> str:
    normalized = secret.replace(" ", "").replace("-", "").upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    key = base64.b32decode(normalized + padding, casefold=True)
    counter = int((time.time() if for_time is None else for_time) // interval)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10**digits)).zfill(digits)


ACCOUNTS_QUERY = """
query GetAccounts {
  accounts {
    id
    displayName
    syncDisabled
    deactivatedAt
    isHidden
    isAsset
    mask
    createdAt
    updatedAt
    displayLastUpdatedAt
    currentBalance
    displayBalance
    includeInNetWorth
    hideFromList
    hideTransactionsFromReports
    includeBalanceInNetWorth
    includeInGoalBalance
    dataProvider
    dataProviderAccountId
    isManual
    transactionsCount
    holdingsCount
    manualInvestmentsTrackingMethod
    order
    logoUrl
    type { name display __typename }
    subtype { name display __typename }
    credential {
      id
      updateRequired
      disconnectedFromDataProviderAt
      dataProvider
      institution { id plaidInstitutionId name status __typename }
      __typename
    }
    institution { id name primaryColor url __typename }
    __typename
  }
  householdPreferences {
    id
    accountGroupOrder
    __typename
  }
}
"""


TRANSACTIONS_QUERY = """
query GetTransactionsList($offset: Int, $limit: Int, $filters: TransactionFilterInput, $orderBy: TransactionOrdering) {
  allTransactions(filters: $filters) {
    totalCount
    results(offset: $offset, limit: $limit, orderBy: $orderBy) {
      id
      amount
      pending
      date
      hideFromReports
      plaidName
      notes
      isRecurring
      reviewStatus
      needsReview
      isSplitTransaction
      createdAt
      updatedAt
      category { id name __typename }
      merchant { name id transactionsCount __typename }
      account { id displayName __typename }
      tags { id name color order __typename }
      __typename
    }
    __typename
  }
  transactionRules {
    id
    __typename
  }
}
"""


RECENT_BALANCES_QUERY = """
query GetAccountRecentBalances($startDate: Date!) {
  accounts {
    id
    recentBalances(startDate: $startDate)
    __typename
  }
}
"""


HOLDINGS_QUERY = """
query Web_GetHoldings($input: PortfolioInput) {
  portfolio(input: $input) {
    aggregateHoldings {
      edges {
        node {
          id
          quantity
          basis
          totalValue
          securityPriceChangeDollars
          securityPriceChangePercent
          lastSyncedAt
          holdings {
            id
            type
            typeDisplay
            name
            ticker
            closingPrice
            isManual
            closingPriceUpdatedAt
            __typename
          }
          security {
            id
            name
            type
            ticker
            typeDisplay
            currentPrice
            currentPriceUpdatedAt
            closingPrice
            closingPriceUpdatedAt
            oneDayChangePercent
            oneDayChangeDollars
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
"""
