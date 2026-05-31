"""Visualizations for the crypto_wallet tracker."""

from __future__ import annotations

from personal_db.ui.aggrid import table_grid


def latest_wallets(con):
    rows = con.execute(
        """
        SELECT label, address, chains, total_networth_usd, holdings_value_usd,
               validation_status, last_validated_at
        FROM crypto_wallet_wallets
        ORDER BY COALESCE(total_networth_usd, holdings_value_usd, 0) DESC
        """
    ).fetchall()
    return table_grid(
        rows,
        headers=[
            "label",
            "address",
            "chains",
            "total_networth_usd",
            "holdings_value_usd",
            "validation_status",
            "last_validated_at",
        ],
    )


def latest_holdings(con):
    rows = con.execute(
        """
        SELECT chain, symbol, name, quantity, usd_price, usd_value, fetched_at
        FROM crypto_wallet_token_balances
        ORDER BY COALESCE(usd_value, 0) DESC
        LIMIT 200
        """
    ).fetchall()
    return table_grid(
        rows,
        headers=["chain", "symbol", "name", "quantity", "usd_price", "usd_value", "fetched_at"],
    )


VIEWS = [
    {
        "name": "wallets",
        "title": "Crypto Wallets",
        "description": "Configured wallets and latest Moralis net-worth validation.",
        "render": latest_wallets,
    },
    {
        "name": "holdings",
        "title": "Crypto Holdings",
        "description": "Latest token balances and USD values returned by Moralis.",
        "render": latest_holdings,
    },
]
