# 🚀 Quick Start Guide - Sprint 4 Fixes

## What Was Fixed?
✅ 7 critical production bugs  
✅ API rate limiting (prevents broker blocks)  
✅ Circuit breaker (stops cascading failures)  
✅ Emergency exit button (panic situations)  
✅ Health check on startup  

## Before First Run

### 1. Update Your config.yml
Add this section (adjust rate for your broker):

```yaml
# ============== Sprint 4: API Rate Limiting ==============
api_rate_limit:
  enabled: true
  max_requests_per_second: 10  # DHAN USERS: SET TO 3!
  burst_size: 15
```

### 2. Backup Your Databases
```bash
copy trade_db.sqlite trade_db.sqlite.backup
copy signal_db.sqlite signal_db.sqlite.backup
```

### 3. Test Import
```bash
cd C:\Rahul\Trade\Strategies\Bot-Stocks
python -c "import api_rate_limiter, circuit_breaker, health_check; print('✅ All imports OK')"
```

## First Test Run

```bash
python scanner.py --once --tf 5m --segment BANKNIFTY
```

**Look for:**
- "Health check" logs on startup
- "Rate limiter initialized" message
- No import errors
- Scanner completes successfully

## 🚨 Emergency Exit Button

**Location:** Dashboard → Open Positions section  
**Button:** Red pulsing "🚨 EMERGENCY EXIT"  

**What it does:**
- Closes ALL positions instantly
- Bypasses normal exit flow
- Sends Telegram alert
- Logs critical warnings

**When to use:** ONLY in panic/emergency situations!

## Key Configuration by Broker

### Dhan (CRITICAL - Strictest Limits)
```yaml
api_rate_limit:
  max_requests_per_second: 3  # DO NOT exceed this!
  burst_size: 5
```

### Flattrade / Shoonya / MStock / OpenAlgo
```yaml
api_rate_limit:
  max_requests_per_second: 10
  burst_size: 15
```

## Circuit Breaker Behavior

**Triggers:** 3 consecutive order failures  
**Action:** Stops all order placement for that scan  
**Alert:** Critical Telegram message sent  
**Reset:** Automatic after 5 minutes  

## Monitoring Checklist

After starting the bot, watch for:
1. ✅ Health check passes all components
2. ✅ Rate limiter logs at configured speed
3. ⚠️ Circuit breaker events (should be rare - investigate if frequent)
4. ⚠️ Partial fill warnings (verify filled quantities)
5. ⚠️ Stale signal rejections (working as intended)

## Production Deployment

⚠️ **MANDATORY STEPS BEFORE LIVE TRADING:**

1. Paper trade for **minimum 1 week**
2. Verify no circuit breaker triggers during normal operation
3. Test emergency exit with 1-2 small positions
4. Confirm Telegram alerts working
5. Monitor logs for any unusual warnings

## File Locations

**New Modules:**
- `api_rate_limiter.py` - Rate limiting
- `circuit_breaker.py` - Failure protection  
- `health_check.py` - Startup validation

**Modified:**
- `app.py` - Thread-safety, health check, emergency endpoint
- `scanner.py` - Timeout, circuit breaker, freshness check
- `trading_adapter.py` - Rate limiting integration
- `trade_db.py` - Unique constraints + indexes
- `config.example.yml` - Rate limit config

**Frontend:**
- `frontend/index.html` - Emergency button
- `frontend/index.css` - Pulse animation
- `frontend/index.js` - Button handler

## Troubleshooting

### "Circuit breaker OPEN" appearing frequently
**Likely causes:**
- Broker API issues
- Invalid credentials
- Rate limits exceeded
- Network problems

**Actions:**
1. Check broker dashboard/status page
2. Verify API keys in config.yml
3. Reduce `max_requests_per_second`
4. Check internet connection

### Emergency exit partially fails
**Actions:**
1. Check Telegram alert for error details
2. Manually close remaining positions via broker
3. Review scanner.log for specifics
4. Report persistent issues

### Rate limiter blocking calls
**This is WORKING AS INTENDED** - throttling to prevent broker blocks.

**If too restrictive:**
1. Check your broker's actual limits
2. Increase `max_requests_per_second` cautiously
3. Reduce number of symbols being scanned

## Support

**Documentation:** See SPRINT4_COMPLETE.md for full details  
**Logs:** scanner.log in Bot-Stocks directory  
**Database:** trade_db.sqlite, signal_db.sqlite

## Status
✅ Implementation: COMPLETE  
✅ Basic Testing: PASSED  
⚠️ Production Ready: AFTER 1 WEEK PAPER TRADING

---
**Last Updated:** 2026-08-28  
**Sprint:** 4 - Production Fixes & Rate Limiting
