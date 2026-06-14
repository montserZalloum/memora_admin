/**
 * Plan Subject Wizard - Frappe Desk page.
 *
 * Drill-down selection:
 *   1. Plan   → 2. Subject → 3. Track → 4. Unit → 5. Topic → 6. Lesson
 *
 * Steps 1-2 have bespoke rendering; steps 3-6 share a generic drill-down
 * driven by the DRILL config below. Selection state lives in `page.state`,
 * so adding a step (e.g. Stage) is just another DRILL entry.
 */

const API = "memora_admin.memora_admin.api.plan_wizard.";

// Step numbers and labels (used by the top progress indicator + breadcrumb).
const STEPS = [
	{ n: 1, key: "plan", label: "Plan" },
	{ n: 2, key: "subject", label: "Subject" },
	{ n: 3, key: "track", label: "Track" },
	{ n: 4, key: "unit", label: "Unit" },
	{ n: 5, key: "topic", label: "Topic" },
	{ n: 6, key: "lesson", label: "Lesson" },
];

// Config for the generic drill-down steps (3-6).
//   method     – whitelisted endpoint to fetch this level's items
//   arg        – endpoint argument name (the parent id)
//   parentId   – extracts the parent id from current state
//   coll       – state key holding the fetched list
//   key        – state key holding the selected item
//   next       – step number to advance to, or null if this is the leaf
const DRILL = {
	3: { label: "Track", method: API + "get_subject_tracks", arg: "subject_id", parentId: (s) => s.subject.subject, coll: "tracks", key: "track", next: 4 },
	4: { label: "Unit", method: API + "get_track_units", arg: "track_id", parentId: (s) => s.track.name, coll: "units", key: "unit", next: 5 },
	5: { label: "Topic", method: API + "get_unit_topics", arg: "unit_id", parentId: (s) => s.unit.name, coll: "topics", key: "topic", next: 6 },
	6: { label: "Lesson", method: API + "get_topic_lessons", arg: "topic_id", parentId: (s) => s.topic.name, coll: "lessons", key: "lesson", next: null },
};

// DocType each step's items live in (used by the Edit buttons).
const EDIT_DOCTYPE = {
	1: "Memora Academic Plan",
	2: "Memora Subject",
	3: "Memora Track",
	4: "Memora Unit",
	5: "Memora Topic",
	6: "Memora Lesson",
};

// "Add" target per step: which DocType to create and the parent fields to
// pre-fill from the current selection so the new record lands in the right
// place. Step 2 (subject) is special-cased — see open_add().
const ADD_CONFIG = {
	1: { doctype: "Memora Academic Plan", defaults: () => ({}) },
	3: { doctype: "Memora Track", defaults: (s) => ({ subject: s.subject.subject }) },
	4: { doctype: "Memora Unit", defaults: (s) => ({ subject: s.subject.subject, track: s.track.name }) },
	5: { doctype: "Memora Topic", defaults: (s) => ({ subject: s.subject.subject, track: s.track.name, unit: s.unit.name }) },
	6: { doctype: "Memora Lesson", defaults: (s) => ({ subject: s.subject.subject, track: s.track.name, unit: s.unit.name, topic: s.topic.name }) },
};

// Steps whose items can be reordered (drag). Plans (step 1) have no order.
const SORTABLE = new Set([2, 3, 4, 5, 6]);

// Resolve, for a sortable step, the save_order `level`, the parent id to scope
// the save, the state collection holding the items, and which field is the
// record id (subjects are keyed by `subject`, the rest by `name`).
function level_info(page, step) {
	if (step === 2) {
		return { level: "subject", parentId: page.state.plan.name, coll: "subjects", idField: "subject", label: "Subject" };
	}
	const cfg = DRILL[step];
	if (!cfg) return null;
	return { level: cfg.key, parentId: cfg.parentId(page.state), coll: cfg.coll, idField: "name", label: cfg.label };
}

// Single cached page instance, so on_page_show can refresh after the admin
// returns from an add/edit form.
let _page = null;
let _skipNextShow = false;

frappe.pages["plan_subject_wizard"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Plan Subject Wizard",
		single_column: true,
	});

	page.main = $(`<div class="plan-subject-wizard"></div>`).appendTo(page.body);
	reset_state(page);
	_page = page;
	_skipNextShow = true; // on_page_show fires right after load; don't double-fetch.

	page.set_secondary_action("Restart", function () {
		reset_state(page);
		render(page);
	});

	load_plans(page);
};

// Re-fetch the current level when the admin returns from an add/edit form so
// new or edited records show up immediately. (Wizard state survives the trip
// because Frappe keeps the page DOM/object cached.)
frappe.pages["plan_subject_wizard"].on_page_show = function () {
	if (_skipNextShow) {
		_skipNextShow = false;
		return;
	}
	if (_page && _page.state) {
		refresh_current_step(_page);
	}
};

function refresh_current_step(page) {
	const step = page.state.step;
	if (step === 1) {
		load_plans(page);
	} else if (step === 2) {
		if (page.state.plan) load_subjects(page);
	} else if (DRILL[step]) {
		load_level(page, step);
	}
}

function reset_state(page) {
	page.state = {
		step: 1,
		sortMode: false, // reorder (drag) mode for the current step
		// selections
		plan: null,
		subject: null,
		track: null,
		unit: null,
		topic: null,
		lesson: null,
		// fetched lists
		plans: [],
		subjects: [],
		tracks: [],
		units: [],
		topics: [],
		lessons: [],
	};
}

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

function load_plans(page) {
	frappe.call({
		method: API + "get_plans",
		callback: function (r) {
			page.state.plans = r.message || [];
			render(page);
		},
	});
}

function load_subjects(page) {
	frappe.call({
		method: API + "get_plan_subjects",
		args: { plan_id: page.state.plan.name },
		freeze: true,
		freeze_message: "Loading subjects...",
		callback: function (r) {
			page.state.subjects = r.message || [];
			render(page);
		},
	});
}

function load_level(page, step) {
	const cfg = DRILL[step];
	frappe.call({
		method: cfg.method,
		args: { [cfg.arg]: cfg.parentId(page.state) },
		freeze: true,
		freeze_message: `Loading ${cfg.label.toLowerCase()}s...`,
		callback: function (r) {
			page.state[cfg.coll] = r.message || [];
			render(page);
		},
	});
}

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------

// Jump back to an earlier step, clearing every selection at or beyond it, then
// reload that step's list (the target's own collection was just cleared).
function go_to_step(page, step) {
	for (const s of STEPS) {
		if (s.n >= step) {
			page.state[s.key] = null;
			const cfg = DRILL[s.n];
			if (cfg) page.state[cfg.coll] = [];
		}
	}
	page.state.step = step;
	page.state.sortMode = false;
	refresh_current_step(page);
}

function select_item(page, cfg, item) {
	page.state[cfg.key] = item;
	page.state.sortMode = false;
	if (cfg.next) {
		page.state.step = cfg.next;
		load_level(page, cfg.next);
	} else {
		on_lesson_selected(page);
	}
}

// Open the standard Frappe form for an existing record (edit).
function open_edit(step, name) {
	frappe.set_route("Form", EDIT_DOCTYPE[step], name);
}

// Create a new record at the given level with the parent chain pre-filled.
function open_add(page, step) {
	// A subject belongs to a plan via the plan's child table, not a Link field,
	// so "Add subject" opens the plan form where that table lives.
	if (step === 2) {
		if (page.state.plan) frappe.set_route("Form", "Memora Academic Plan", page.state.plan.name);
		return;
	}
	const cfg = ADD_CONFIG[step];
	if (!cfg) return;
	// route_options are applied as default field values on the new form.
	frappe.route_options = cfg.defaults(page.state);
	frappe.new_doc(cfg.doctype);
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function render(page) {
	let body = "";
	const step = page.state.step;
	if (page.state.sortMode && SORTABLE.has(step)) {
		body = render_sort_mode(page, step);
	} else if (step === 1) {
		body = render_step_plans(page);
	} else if (step === 2) {
		body = render_step_subjects(page);
	} else {
		body = render_drill(page, step);
	}

	page.main.html(`
		${render_steps_indicator(page)}
		${page.state.step > 1 ? render_breadcrumb(page) : ""}
		<div class="wizard-body mt-3">${body}</div>
	`);

	bind_events(page);
}

function render_steps_indicator(page) {
	return `
		<div class="wizard-steps d-flex align-items-center flex-wrap gap-1">
			${STEPS.map((s, i) => {
				const active = page.state.step === s.n;
				const done = page.state.step > s.n;
				const cls = active ? "text-primary font-weight-bold" : done ? "text-success" : "text-muted";
				const sep = i < STEPS.length - 1 ? `<span class="text-muted mx-1">›</span>` : "";
				return `<span class="${cls}">${s.n}. ${s.label}</span>${sep}`;
			}).join("")}
		</div>
	`;
}

// Clickable trail of chosen values for the locked-in (earlier) steps.
function render_breadcrumb(page) {
	const labelers = {
		plan: (p) => p.plan_name || p.name,
		subject: (s) => s.title,
		track: (t) => t.title,
		unit: (u) => u.title,
		topic: (t) => t.title,
	};
	const parts = [];
	for (const s of STEPS) {
		const sel = page.state[s.key];
		// Show a crumb only for steps already completed (current step is the chooser).
		if (sel && page.state.step > s.n && labelers[s.key]) {
			parts.push(
				`<a href="#" class="bc-jump" data-step="${s.n}">${s.label}: <strong>${frappe.utils.escape_html(
					labelers[s.key](sel)
				)}</strong></a>`
			);
		}
	}
	if (!parts.length) return "";
	return `<div class="wizard-breadcrumb mt-2 text-muted">${parts.join(
		`<span class="mx-1">/</span>`
	)}</div>`;
}

function render_step_plans(page) {
	const rows = page.state.plans
		.map(
			(p) => `
		<tr class="select-plan wiz-item" data-plan="${frappe.utils.escape_html(p.name)}"
			${search_attr(p.plan_name, p.name, p.grade_title, p.major_title, p.season_title)} style="cursor:pointer;">
			<td>${frappe.utils.escape_html(p.plan_name || p.name)}${id_line(p.name)}</td>
			<td>${frappe.utils.escape_html(p.grade_title || "-")}</td>
			<td>${frappe.utils.escape_html(p.major_title || "-")}</td>
			<td>${frappe.utils.escape_html(p.season_title || "-")}</td>
			<td>${pill(p.is_published ? "green" : "gray", p.is_published ? "Published" : "Draft")}</td>
			<td class="text-right">
				${edit_button(1, p.name)}
				<button class="btn btn-xs btn-primary">Select</button>
			</td>
		</tr>`
		)
		.join("");

	return `
		<div class="frappe-card">
			<div class="card-body">
				${header_row("Step 1 — Choose an Academic Plan", add_button(1, "Plan"))}
				${search_box("Search plans...")}
				<table class="table table-hover">
					<thead>
						<tr><th>Plan</th><th>Grade</th><th>Major</th><th>Season</th><th>Status</th><th></th></tr>
					</thead>
					<tbody>
						${rows || `<tr><td colspan="6" class="text-muted text-center">No plans found.</td></tr>`}
						<tr class="wiz-empty" style="display:none;"><td colspan="6" class="text-muted text-center">No matches.</td></tr>
					</tbody>
				</table>
			</div>
		</div>
	`;
}

function render_step_subjects(page) {
	const cards = page.state.subjects
		.map(
			(s) => `
		<div class="col-md-4 mb-3 wiz-item" ${search_attr(s.title, s.subject)}>
			<div class="frappe-card select-subject h-100" data-subject="${frappe.utils.escape_html(s.subject)}" style="cursor:pointer;">
				<div class="card-body">
					<div class="d-flex justify-content-between align-items-start">
						<h6 class="mb-1">${frappe.utils.escape_html(s.title || s.subject)}</h6>
						${edit_button(2, s.subject)}
					</div>
					${id_line(s.subject)}
					<div class="d-flex flex-wrap gap-1 mt-2">
						${pill(s.is_premium ? "orange" : "blue", s.is_premium ? "Premium" : "Free")}
						${pill(s.is_published ? "green" : "gray", s.is_published ? "Published" : "Draft")}
						${s.in_linear ? pill("purple", "Linear") : ""}
					</div>
				</div>
			</div>
		</div>`
		)
		.join("");

	return `
		<div class="frappe-card">
			<div class="card-body">
				${header_row("Step 2 — Choose a Subject", step_actions(2, "Subject to Plan"), 1)}
				${search_box("Search subjects...")}
				<div class="row mt-3">
					${cards || `<div class="col-12"><p class="text-muted text-center">This plan has no subjects.</p></div>`}
					${no_results_row()}
				</div>
			</div>
		</div>
	`;
}

function render_drill(page, step) {
	const cfg = DRILL[step];
	const items = page.state[cfg.coll] || [];
	const cards = items.map((it) => render_drill_card(it, step)).join("");

	return `
		<div class="frappe-card">
			<div class="card-body">
				${header_row(`Step ${step} — Choose a ${cfg.label}`, step_actions(step, cfg.label), step - 1)}
				${search_box(`Search ${cfg.label.toLowerCase()}s...`)}
				<div class="row mt-3">
					${cards || `<div class="col-12"><p class="text-muted text-center">No ${cfg.label.toLowerCase()}s here.</p></div>`}
					${no_results_row()}
				</div>
			</div>
		</div>
	`;
}

function render_drill_card(it, step) {
	const badges = [pill(it.is_published ? "green" : "gray", it.is_published ? "Published" : "Draft")];
	if ("is_free" in it) badges.push(pill(it.is_free ? "blue" : "orange", it.is_free ? "Free" : "Premium"));
	if (it.is_linear) badges.push(pill("purple", "Linear"));
	if (it.is_reviewable) badges.push(pill("green", "Reviewable"));

	return `
		<div class="col-md-4 mb-3 wiz-item" ${search_attr(it.title, it.name)}>
			<div class="frappe-card select-drill h-100" data-name="${frappe.utils.escape_html(it.name)}" style="cursor:pointer;">
				<div class="card-body">
					<div class="d-flex justify-content-between align-items-start">
						<h6 class="mb-1">${frappe.utils.escape_html(it.title || it.name)}</h6>
						${edit_button(step, it.name)}
					</div>
					${id_line(it.name)}
					<div class="d-flex flex-wrap gap-1 mt-2">${badges.join("")}</div>
				</div>
			</div>
		</div>
	`;
}

// Drag-to-reorder list for the current step (toggled via the Sort button).
function render_sort_mode(page, step) {
	const info = level_info(page, step);
	const items = page.state[info.coll] || [];

	const rows = items
		.map((it) => {
			const id = it[info.idField];
			const title = it.title || id;
			return `
		<div class="wiz-sort-item" data-id="${frappe.utils.escape_html(id)}">
			<span class="wiz-sort-handle" title="Drag to reorder">&#x2630;</span>
			<span class="wiz-sort-title">${frappe.utils.escape_html(title)}</span>
			<span class="text-muted small">${frappe.utils.escape_html(id)}</span>
		</div>`;
		})
		.join("");

	const actions = `<div class="d-flex gap-1">
		<button class="btn btn-xs btn-default wiz-sort-cancel">Cancel</button>
		<button class="btn btn-xs btn-primary wiz-sort-save">Save order</button>
	</div>`;

	return `
		<div class="frappe-card">
			<div class="card-body">
				${header_row(`Step ${step} — Reorder ${info.label}s`, actions)}
				<p class="text-muted">Drag the items by the handle to change their order, then click <b>Save order</b>.</p>
				<div class="wiz-sort-list">
					${rows || `<p class="text-muted text-center">Nothing to reorder here.</p>`}
				</div>
				<style>
					.wiz-sort-list { display: flex; flex-direction: column; gap: 6px; }
					.wiz-sort-item {
						display: flex; align-items: center; gap: 10px;
						padding: 8px 10px; border: 1px solid var(--border-color);
						border-radius: var(--border-radius); background: var(--card-bg);
					}
					.wiz-sort-handle { cursor: grab; color: var(--text-muted); }
					.wiz-sort-title { flex: 1; }
					.wiz-sort-ghost { opacity: 0.4; }
				</style>
			</div>
		</div>
	`;
}

function pill(color, text) {
	return `<span class="indicator-pill ${color}">${frappe.utils.escape_html(text)}</span>`;
}

// "+ Add <Label>" button shown in a step's header.
function add_button(step, label) {
	return `<button class="btn btn-xs btn-default wiz-add" data-step="${step}">
		<i class="fa fa-plus"></i> Add ${frappe.utils.escape_html(label)}</button>`;
}

// "Sort" toggle button shown beside Add (enters drag-reorder mode).
function sort_button(step) {
	return `<button class="btn btn-xs btn-default wiz-sort-toggle mx-1" data-step="${step}" title="Reorder">
		<i class="fa fa-sort"></i> Sort</button>`;
}

// Sort + Add buttons grouped in one wrapper for a step header.
function step_actions(step, add_label) {
	return `<div class="d-flex gap-1">${sort_button(step)}${add_button(step, add_label)}</div>`;
}

// Muted record-id line shown inside each card/row.
function id_line(id) {
	return `<div class="text-muted small wiz-id">${frappe.utils.escape_html(id)}</div>`;
}

// Pencil "Edit" button shown on a card/row.
function edit_button(step, name) {
	return `<button class="btn btn-xs btn-default wiz-edit" data-edit-step="${step}"
		data-name="${frappe.utils.escape_html(name)}" title="Edit"><i class="fa fa-pencil"></i></button>`;
}

// A card-title row with a heading on the left and an action on the right.
// `backStep` (optional) renders a back-arrow before the title that returns to
// that step.
function header_row(title, action, backStep) {
	const back = backStep
		? `<button class="btn btn-xs btn-default wiz-back mr-2" data-step="${backStep}" title="Back">
			<i class="fa fa-arrow-left"></i></button>`
		: "";
	return `<div class="d-flex justify-content-between align-items-center mb-2">
		<h5 class="card-title mb-0 d-flex align-items-center">${back}${title}</h5>${action || ""}</div>`;
}

// Client-side search box. Filtering happens in the browser against the already
// loaded list (see the `.wiz-search` handler) — no server round-trip.
function search_box(placeholder) {
	return `<div class="mb-3" style="max-width:320px;">
		<input type="text" class="form-control wiz-search" placeholder="${frappe.utils.escape_html(placeholder)}" />
	</div>`;
}

// Lowercased haystack used by the client-side filter; safe inside an HTML attr.
function search_attr(...parts) {
	const hay = parts.filter(Boolean).join(" ").toLowerCase();
	return `data-search="${frappe.utils.escape_html(hay)}"`;
}

// Shown when a search filters every item out.
function no_results_row() {
	return `<div class="col-12 wiz-empty" style="display:none;">
		<p class="text-muted text-center">No matches.</p></div>`;
}

// ---------------------------------------------------------------------------
// Event binding
// ---------------------------------------------------------------------------

function bind_events(page) {
	// Breadcrumb back-navigation (available on every step > 1).
	page.main.find(".bc-jump").on("click", function (e) {
		e.preventDefault();
		go_to_step(page, $(this).data("step"));
	});

	// Back-arrow in the step header → previous step.
	page.main.find(".wiz-back").on("click", function (e) {
		e.stopPropagation();
		go_to_step(page, $(this).data("step"));
	});

	// Add / Edit buttons (present on every step). stopPropagation so clicking
	// them doesn't also trigger the card/row select handler.
	page.main.find(".wiz-add").on("click", function (e) {
		e.stopPropagation();
		open_add(page, $(this).data("step"));
	});
	page.main.find(".wiz-edit").on("click", function (e) {
		e.stopPropagation();
		open_edit($(this).data("edit-step"), $(this).data("name"));
	});

	// Client-side search: filter the loaded items in the DOM (no re-render, so
	// the input keeps focus). Present on every step.
	page.main.find(".wiz-search").on("input", function () {
		const q = $(this).val().trim().toLowerCase();
		let visible = 0;
		page.main.find(".wiz-item").each(function () {
			const match = !q || ($(this).attr("data-search") || "").indexOf(q) !== -1;
			$(this).toggle(match);
			if (match) visible++;
		});
		page.main.find(".wiz-empty").toggle(visible === 0);
	});

	// Reorder (drag) mode: wire the sortable list + save/cancel and stop here.
	if (page.state.sortMode && SORTABLE.has(page.state.step)) {
		const listEl = page.main.find(".wiz-sort-list")[0];
		if (listEl && typeof Sortable !== "undefined") {
			new Sortable(listEl, { handle: ".wiz-sort-handle", animation: 150, ghostClass: "wiz-sort-ghost" });
		}
		page.main.find(".wiz-sort-cancel").on("click", function () {
			page.state.sortMode = false;
			render(page);
		});
		page.main.find(".wiz-sort-save").on("click", function () {
			save_order(page);
		});
		return;
	}

	// Enter reorder mode (Sort button, present on sortable steps).
	page.main.find(".wiz-sort-toggle").on("click", function () {
		page.state.sortMode = true;
		render(page);
	});

	if (page.state.step === 1) {
		page.main.find(".select-plan").on("click", function () {
			const planName = $(this).data("plan");
			page.state.plan = page.state.plans.find((p) => p.name === planName);
			page.state.step = 2;
			load_subjects(page);
		});
	} else if (page.state.step === 2) {
		page.main.find(".select-subject").on("click", function () {
			const subjectId = $(this).data("subject");
			page.state.subject = page.state.subjects.find((s) => s.subject === subjectId);
			page.state.step = 3;
			load_level(page, 3);
		});
	} else {
		const cfg = DRILL[page.state.step];
		page.main.find(".select-drill").on("click", function () {
			const name = $(this).data("name");
			const item = (page.state[cfg.coll] || []).find((x) => x.name === name);
			select_item(page, cfg, item);
		});
	}
}

function on_lesson_selected(page) {
	// Leaf reached — open the selected lesson's form.
	frappe.set_route("Form", "Memora Lesson", page.state.lesson.name);
}

// Persist the dragged order of the current step, then reload it in that order.
function save_order(page) {
	const info = level_info(page, page.state.step);
	const ordered = page.main
		.find(".wiz-sort-item")
		.map((i, el) => $(el).attr("data-id"))
		.get();

	frappe.call({
		method: API + "save_order",
		args: { level: info.level, parent_id: info.parentId, ordered_ids: JSON.stringify(ordered) },
		freeze: true,
		freeze_message: "Saving order...",
		callback: function () {
			frappe.show_alert({ message: "Order saved.", indicator: "green" }, 4);
			page.state.sortMode = false;
			refresh_current_step(page);
		},
	});
}
