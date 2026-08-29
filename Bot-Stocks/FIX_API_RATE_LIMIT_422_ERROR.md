# Fix: API Config 422 Error - Added api_rate_limit to Pydantic Model

## Issue
After adding `api_rate_limit` section to `config.yml`, the frontend was getting 422 errors:
```
INFO: 127.0.0.1:54400 - "POST /api/config HTTP/1.1" 422 Unprocessable Content
```

## Root Cause
The backend's Pydantic validation model (`ConfigUpdateRequest` in `app.py`) did not include the `api_rate_limit` field. When the frontend sent the complete config (including the new section), FastAPI rejected it as invalid.

## Solution
Added the missing Pydantic models to `app.py`:

### 1. Created `ApiRateLimitConfig` Model
```python
class ApiRateLimitConfig(BaseModel):
    enabled: bool = True
    max_requests_per_second: int = 10
    burst_size: int = 15
```

### 2. Added to `ConfigUpdateRequest`
```python
class ConfigUpdateRequest(BaseModel):
    # ... existing fields ...
    api_rate_limit: ApiRateLimitConfig = ApiRateLimitConfig()  # ← NEW
    data: dict
    bot: BotConfig
    symbols: list[str]
```

## Files Modified
- `app.py` (lines 151-154, 216)

## Testing
1. **Restart the Flask/FastAPI server** (Ctrl+C, then `python app.py`)
2. Frontend config updates should now work without 422 errors
3. The `api_rate_limit` section will be properly saved to `config.yml`

## Note: OpenAlgo Data Errors Are Separate
The other errors you saw:
```
[YESBANK] openalgo error: No data available for the specified period
```

These are **NOT** caused by the dashboard changes. They indicate:
- OpenAlgo cannot fetch historical data for those symbols
- Possible causes:
  - Symbol not available on that exchange
  - Trading session closed (weekends/holidays)
  - OpenAlgo server connectivity issues
  - Wrong timeframe requested

## Summary
✅ Fixed: 422 error when toggling dashboard controls  
✅ Fixed: Config save now includes `api_rate_limit`  
⚠️ Unrelated: OpenAlgo data fetching errors (broker/data source issue)
