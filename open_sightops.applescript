on run
	set dashboardURL to "http://127.0.0.1:3000/ops-kanban.html"
	try
		do shell script "curl -sf -o /dev/null --max-time 2 " & quoted form of dashboardURL
	on error
		do shell script "/Users/nathanthompson/.hermes/hermes-agent/venv/bin/python3.11 /Users/nathanthompson/.hermes/profiles/max-ea/scripts/dashboard_server.py 3000 >/Users/nathanthompson/.hermes/profiles/max-ea/logs/dashboard-server-fallback.log 2>&1 &"
		delay 0.8
	end try
	open location dashboardURL
end run
