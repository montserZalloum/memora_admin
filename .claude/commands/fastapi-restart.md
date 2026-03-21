Restart the FastAPI server by following these steps exactly:

1. Find the uvicorn master process PID and send SIGHUP to fully reload workers with new code:
   ```bash
   sudo kill -HUP $(pgrep -of 'uvicorn fastapi_app.main:app')
   ```

2. Wait 3 seconds for workers to restart:
   ```bash
   sleep 3
   ```

3. Verify the server is back up:
   ```bash
   curl -s http://127.0.0.1:8002/api/v1/health/live
   ```

4. Report the result. Expected success response: `{"status":"alive","api_version":"v1"}`

If the health check fails, wait 2 more seconds and retry once. If it still fails, report the error.
