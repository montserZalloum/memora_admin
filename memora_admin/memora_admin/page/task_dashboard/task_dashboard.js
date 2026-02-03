/**
 * Task Dashboard - Frappe Desk page for scheduled task administration.
 *
 * Shows recent task runs and allows manual triggering with confirmation.
 * Per CONTEXT.md: Admins need visibility into task execution and ability to manually trigger.
 */

frappe.pages["task_dashboard"].on_page_load = function (wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Scheduled Tasks Dashboard",
		single_column: true,
	});

	// Store page reference
	page.main = $(`<div class="task-dashboard"></div>`).appendTo(page.body);

	// Define available tasks
	// These map to task functions in memora_admin.memora_admin.tasks.*
	page.tasks = [
		{
			name: "streak_reset",
			label: "Streak Reset",
			schedule: "Daily at 00:05 (Amman)",
		},
		{
			name: "session_cleanup",
			label: "Session Cleanup",
			schedule: "Hourly at :15",
		},
		{
			name: "leaderboard_daily",
			label: "Daily Leaderboard Archive",
			schedule: "Daily at 00:10 (Amman)",
		},
		{
			name: "leaderboard_weekly",
			label: "Weekly Leaderboard Archive",
			schedule: "Friday at 00:15 (Amman)",
		},
	];

	// Render page
	render_dashboard(page);
	load_task_history(page);

	// Refresh button
	page.set_secondary_action("Refresh", function () {
		load_task_history(page);
	});
};

function render_dashboard(page) {
	let html = `
		<div class="frappe-card mb-4">
			<div class="card-body">
				<h5 class="card-title">Manual Task Trigger</h5>
				<p class="text-muted">Click a task to trigger it manually. Tasks will run with "Manual" trigger type.</p>
				<div class="task-buttons d-flex flex-wrap gap-2">
					${page.tasks
						.map(
							(t) => `
						<button class="btn btn-outline-primary trigger-task" data-task="${t.name}">
							<span class="icon"><i class="fa fa-play"></i></span>
							${t.label}
						</button>
					`
						)
						.join("")}
				</div>
			</div>
		</div>
		<div class="frappe-card">
			<div class="card-body">
				<h5 class="card-title">Recent Task Runs</h5>
				<p class="text-muted mb-3">Showing last 50 task executions</p>
				<div class="task-history-table"></div>
			</div>
		</div>
	`;
	page.main.html(html);

	// Bind trigger buttons
	page.main.find(".trigger-task").on("click", function () {
		let task = $(this).data("task");
		let taskInfo = page.tasks.find((t) => t.name === task);
		trigger_task_with_confirm(task, taskInfo.label, page);
	});
}

function trigger_task_with_confirm(task_name, task_label, page) {
	frappe.confirm(
		`Are you sure you want to manually trigger <strong>${task_label}</strong>?<br><br>
		 This will run the task immediately with triggered_by="Manual".`,
		function () {
			// Yes - trigger the task
			frappe.call({
				method: "memora_admin.memora_admin.api.task_admin.trigger_task",
				args: { task_name: task_name },
				freeze: true,
				freeze_message: `Running ${task_label}...`,
				callback: function (r) {
					if (r.message && r.message.success) {
						frappe.show_alert(
							{
								message: `${task_label} completed successfully`,
								indicator: "green",
							},
							5
						);
					} else {
						frappe.show_alert(
							{
								message: `${task_label} failed: ${r.message ? r.message.error : "Unknown error"}`,
								indicator: "red",
							},
							7
						);
					}
					// Refresh history after trigger
					load_task_history(page);
				},
				error: function () {
					frappe.show_alert(
						{
							message: `Failed to trigger ${task_label}. Check permissions.`,
							indicator: "red",
						},
						7
					);
				},
			});
		},
		function () {
			// No - do nothing
		}
	);
}

function load_task_history(page) {
	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Memora Task Run Log",
			fields: [
				"name",
				"task_name",
				"run_date",
				"started_at",
				"status",
				"duration_sec",
				"processed_count",
				"failed_count",
				"triggered_by",
			],
			limit_page_length: 50,
			order_by: "started_at desc",
		},
		callback: function (r) {
			if (r.message) {
				render_history_table(page, r.message);
			}
		},
	});
}

function render_history_table(page, data) {
	let statusColors = {
		Success: "green",
		Failed: "red",
		Partial: "orange",
	};

	let html = `
		<table class="table table-bordered table-hover">
			<thead>
				<tr>
					<th>Task</th>
					<th>Date</th>
					<th>Started</th>
					<th>Status</th>
					<th>Duration</th>
					<th>Processed</th>
					<th>Failed</th>
					<th>Trigger</th>
				</tr>
			</thead>
			<tbody>
				${data
					.map(
						(row) => `
					<tr>
						<td><a href="/app/memora-task-run-log/${row.name}">${row.task_name}</a></td>
						<td>${row.run_date}</td>
						<td>${frappe.datetime.str_to_user(row.started_at)}</td>
						<td><span class="indicator-pill ${statusColors[row.status] || "gray"}">${row.status}</span></td>
						<td>${row.duration_sec ? row.duration_sec.toFixed(2) + "s" : "-"}</td>
						<td>${row.processed_count || 0}</td>
						<td>${row.failed_count || 0}</td>
						<td>${row.triggered_by || "Scheduler"}</td>
					</tr>
				`
					)
					.join("")}
			</tbody>
		</table>
	`;

	if (data.length === 0) {
		html = '<p class="text-muted">No task runs recorded yet.</p>';
	}

	page.main.find(".task-history-table").html(html);
}
