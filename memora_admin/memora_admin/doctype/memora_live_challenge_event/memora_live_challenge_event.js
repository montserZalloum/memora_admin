// Copyright (c) 2026, corex and contributors
// For license information, please see license.txt

frappe.ui.form.on("Memora Live Challenge Event", {
	refresh(frm) {
		// Status indicator colors
		if (frm.doc.status === "Draft") {
			frm.page.set_indicator("Draft", "orange");
		} else if (frm.doc.status === "Waiting") {
			frm.page.set_indicator("Waiting", "blue");
		} else if (frm.doc.status === "Active") {
			frm.page.set_indicator("Active", "green");
		} else if (frm.doc.status === "Ended") {
			frm.page.set_indicator("Ended", "darkgrey");
		}

		// Freeze form when not in Draft
		if (frm.doc.status !== "Draft" && !frm.is_new()) {
			frm.disable_save();
			frm.set_read_only();
		}

		// Read-only indicators for computed fields
		frm.set_df_property("exam_start_ts", "read_only", 1);
		frm.set_df_property("exam_end_ts", "read_only", 1);

		// Apply question timer logic on refresh
		_apply_question_timer(frm);

		// Live Participants button (only during Active)
		if (frm.doc.status === "Active") {
			frm.add_custom_button(__("Live Participants"), function () {
				_open_live_participants_dialog(frm.doc.name);
			});
		}

		// Show Leaderboard button (only when leaderboard is computed)
		if (frm.doc.status === "Ended" && frm.doc.leaderboard_json) {
			frm.add_custom_button(__("Show Leaderboard"), function () {
				frappe.call({
					method: "memora_admin.memora_admin.api.live_challenge.get_full_leaderboard",
					args: { event_id: frm.doc.name },
					callback: function (r) {
						if (!r.message || !r.message.length) {
							frappe.msgprint(__("No leaderboard data available."));
							return;
						}
						_show_leaderboard_dialog(r.message);
					},
				});
			});
		}

	},

	scheduled_start(frm) {
		let val = frm.doc.scheduled_start;
		if (val && val.length > 16) {
			let zeroed = val.slice(0, 16) + ":00";
			if (zeroed !== val) {
				frm.set_value("scheduled_start", zeroed);
			}
		}
	},

	enable_question_timer(frm) {
		_apply_question_timer(frm);
	},

	question_time_limit(frm) {
		_calc_exam_duration(frm);
	},
});

frappe.ui.form.on("Memora Live Challenge Question", {
	questions_add(frm) {
		_calc_exam_duration(frm);
	},
	questions_remove(frm) {
		_calc_exam_duration(frm);
	},
});

function _apply_question_timer(frm) {
	frm.toggle_display("question_time_limit", frm.doc.enable_question_timer);
	frm.toggle_display("exam_duration", !frm.doc.enable_question_timer);
	if (frm.doc.enable_question_timer) {
		_calc_exam_duration(frm);
	}
}

function _calc_exam_duration(frm) {
	if (!frm.doc.enable_question_timer) return;
	let count = (frm.doc.questions || []).length;
	let limit = cint(frm.doc.question_time_limit) || 30;
	let minutes = Math.ceil((limit * count) / 60);
	frm.set_value("exam_duration", Math.max(minutes, 1));
}

function _show_leaderboard_dialog(data) {
	let rows = data
		.map(
			(r) =>
				`<tr>
				<td style="text-align:center;font-weight:${r.rank <= 3 ? "bold" : "normal"}">${r.rank}</td>
				<td>${frappe.utils.escape_html(r.display_name)}</td>
				<td>${frappe.utils.escape_html(r.player)}</td>
				<td style="text-align:right">${r.score}</td>
			</tr>`
		)
		.join("");

	let html = `
		<div style="max-height:400px;overflow-y:auto">
			<table class="table table-bordered table-hover" style="margin:0">
				<thead><tr>
					<th style="text-align:center;width:60px">Rank</th>
					<th>Name</th>
					<th>Player ID</th>
					<th style="text-align:right;width:80px">Score</th>
				</tr></thead>
				<tbody>${rows}</tbody>
			</table>
		</div>
		<p class="text-muted" style="margin-top:8px">${data.length} participants</p>
	`;

	let d = new frappe.ui.Dialog({
		title: __("Leaderboard ({0} participants)", [data.length]),
		size: "large",
	});
	d.$body.html(html);
	d.show();
}

function _open_live_participants_dialog(event_id) {
	let d = new frappe.ui.Dialog({
		title: __("Live Participants"),
		size: "large",
	});

	d.$body.html('<div class="text-center text-muted" style="padding:40px">Loading...</div>');
	d.show();

	let interval_id = null;

	function _fetch_and_render() {
		frappe.call({
			method: "memora_admin.memora_admin.api.live_challenge.get_live_participants",
			args: { event_id: event_id },
			async: true,
			callback: function (r) {
				if (!r.message) return;
				let data = r.message;

				if (data.ended) {
					if (interval_id) {
						clearInterval(interval_id);
						interval_id = null;
					}
					d.$body.html(
						'<div class="alert alert-warning" style="margin:20px">' +
						'<strong>Event ' + frappe.utils.escape_html(data.status) + '</strong> — ' +
						'Live participant monitoring has stopped.' +
						'</div>'
					);
					return;
				}

				let parts = data.participants || [];

				let rows = parts
					.map(function (p) {
						let status_badge =
							p.status === "Submitted"
								? '<span class="indicator-pill green">Submitted</span>'
								: '<span class="indicator-pill orange">Taking exam</span>';
						let score_cell = p.score !== null ? p.score : "-";
						let submitted_cell = p.submitted_at || "-";
						return (
							"<tr>" +
							"<td>" + frappe.utils.escape_html(p.display_name) + "</td>" +
							"<td>" + frappe.utils.escape_html(p.player) + "</td>" +
							"<td>" + status_badge + "</td>" +
							'<td style="text-align:right">' + score_cell + "</td>" +
							"<td>" + submitted_cell + "</td>" +
							"<td>" + (p.joined_at || "-") + "</td>" +
							"</tr>"
						);
					})
					.join("");

				let html =
					'<div style="margin-bottom:10px">' +
					'<span class="badge badge-primary" style="margin-right:8px">' +
					data.joined_count + " Joined</span>" +
					'<span class="badge badge-success" style="margin-right:8px">' +
					data.submitted_count + " Submitted</span>" +
					'<span class="badge badge-warning">' +
					data.still_taking + " Taking exam</span>" +
					"</div>" +
					'<div style="max-height:400px;overflow-y:auto">' +
					'<table class="table table-bordered table-hover" style="margin:0">' +
					"<thead><tr>" +
					"<th>Name</th>" +
					"<th>Player ID</th>" +
					"<th>Status</th>" +
					'<th style="text-align:right;width:80px">Score</th>' +
					"<th>Submitted At</th>" +
					"<th>Joined At</th>" +
					"</tr></thead>" +
					"<tbody>" + rows + "</tbody>" +
					"</table></div>" +
					'<p class="text-muted" style="margin-top:8px">Auto-refreshes every minute</p>';

				d.$body.html(html);
			},
		});
	}

	_fetch_and_render();
	interval_id = setInterval(_fetch_and_render, 60000);

	d.onhide = function () {
		if (interval_id) {
			clearInterval(interval_id);
			interval_id = null;
		}
	};
}
