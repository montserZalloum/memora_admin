Restart the FastAPI server by following these steps exactly:

1. Kill the uvicorn process on port 8002:
   ```bash
   fuser -k 8002/tcp
   ```

2. Wait 3 seconds for the process supervisor to auto-restart it:
   ```bash
   sleep 3
   ```

3. Verify the server is back up:
   ```bash
   curl -s http://127.0.0.1:8002/api/v1/health/live
   ```

4. Report the result. Expected success response: `{"status":"alive","api_version":"v1"}`

If the health check fails, wait 2 more seconds and retry once. If it still fails, report the error.
