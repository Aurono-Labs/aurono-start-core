# ============================================================
# Domain rejection reason codes (authoritative)
# ============================================================

# ---- Market data ----
INVALID_MARKET_DATA        = "INVALID_MARKET_DATA"

# ---- Capital / inventory ----
CAPITAL_INSUFFICIENT       = "CAPITAL_INSUFFICIENT"
INVENTORY_INSUFFICIENT     = "INVENTORY_INSUFFICIENT"

# ---- Strategy rules ----
NO_SIGNAL                  = "NO_SIGNAL"
NO_ACB                     = "NO_ACB"
BELOW_ACB                  = "BELOW_ACB"
BUY_TRIGGERED              = "BUY_TRIGGERED"
SELL_TRIGGERED             = "SELL_TRIGGERED"
BUY_DISABLED               = "BUY_DISABLED"
SELL_DISABLED              = "SELL_DISABLED"

# ---- Constraints ----
MIN_POSITION_BREACH        = "MIN_POSITION_BREACH"
COOLDOWN_ACTIVE            = "COOLDOWN_ACTIVE"
BELOW_MIN_NOTIONAL         = "BELOW_MIN_NOTIONAL"
BELOW_MIN_ORDER_SIZE       = "BELOW_MIN_ORDER_SIZE"
RSI_NOT_OVERSOLD           = "RSI_NOT_OVERSOLD"
RSI_NOT_OVERBOUGHT         = "RSI_NOT_OVERBOUGHT"

# ---- Adapter-mapped ----
EXCHANGE_UNAVAILABLE       = "EXCHANGE_UNAVAILABLE"   # call to exchange failed mid-flight
EXCHANGE_UNREACHABLE       = "EXCHANGE_UNREACHABLE"   # pre-flight gate: health monitor reports exchange down

