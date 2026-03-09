app_name = "memora_admin"
app_title = "Memora Admin"
app_publisher = "corex"
app_description = "Memora"
app_email = "dev@corex.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "memora_admin",
# 		"logo": "/assets/memora_admin/logo.png",
# 		"title": "Memora Admin",
# 		"route": "/memora_admin",
# 		"has_permission": "memora_admin.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/memora_admin/css/memora_admin.css"
app_include_js = "/assets/memora_admin/js/admin_filter_helper.js"

# include js, css files in header of web template
# web_include_css = "/assets/memora_admin/css/memora_admin.css"
# web_include_js = "/assets/memora_admin/js/memora_admin.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "memora_admin/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {
	"Memora Player Profile": "memora_admin/doctype/memora_player_profile/memora_player_profile.js",
	"Memora Lesson": "public/js/game_lesson.js",
}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "memora_admin/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "memora_admin.utils.jinja_methods",
# 	"filters": "memora_admin.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "memora_admin.install.before_install"
after_install = "memora_admin.memora_admin.setup.after_install"
before_migrate = "memora_admin.memora_admin.setup.before_migrate"
after_migrate = "memora_admin.memora_admin.setup.after_migrate"

# Uninstallation
# ------------

# before_uninstall = "memora_admin.uninstall.before_uninstall"
# after_uninstall = "memora_admin.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

fixtures = [
	{
		"dt": "Workspace",
		"filters": [["name", "in", ["Memora", "Memora Library"]]],
	},
	{
		"dt": "Default Workspace Sidebar",
		"filters": [["name", "in", ["Memora", "Memora Library"]]],
	},
]

# before_app_install = "memora_admin.utils.before_app_install"
# after_app_install = "memora_admin.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "memora_admin.utils.before_app_uninstall"
# after_app_uninstall = "memora_admin.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "memora_admin.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Memora Season": {
		"after_insert": "memora_admin.events.access_sync.on_season_updated",
		"on_update": "memora_admin.events.access_sync.on_season_updated",
		"on_trash": "memora_admin.events.access_sync.on_season_deleted",
	},
	"Memora Player Subscription": {
		"after_insert": "memora_admin.events.access_sync.on_subscription_change",
		"on_update": "memora_admin.events.access_sync.on_subscription_change",
		"on_trash": "memora_admin.events.access_sync.on_subscription_deleted",
	},
	"Memora Player Profile": {
		"after_insert": "memora_admin.events.profile_sync.on_player_profile_updated",
		"on_update": [
			"memora_admin.events.device_sync.on_player_profile_update",
			"memora_admin.events.profile_sync.on_player_profile_updated",
			"memora_admin.events.plan_change_sync.on_player_profile_plan_changed",
		],
	},
	# Product catalog cache invalidation
	"Memora Product Grant": {
		"after_insert": "memora_admin.events.catalog_sync.on_product_grant_changed",
		"on_update": "memora_admin.events.catalog_sync.on_product_grant_changed",
		"on_trash": "memora_admin.events.catalog_sync.on_product_grant_changed",
	},
	# Purchase request admin notification
	"Memora Subscription Transaction": {
		"after_insert": "memora_admin.events.purchase_sync.on_purchase_request_created",
	},
	# Content report admin notification
	"Memora Content Report": {
		"after_insert": "memora_admin.events.report_sync.on_content_report_created",
	},
	# Build trigger events for content DocTypes (debounced)
	"Memora Subject": {
		"on_update": "memora_admin.events.build_trigger.on_content_updated",
		"on_trash": "memora_admin.events.build_trigger.on_content_updated",
	},
	"Memora Track": {
		"on_update": "memora_admin.events.build_trigger.on_content_updated",
		"on_trash": "memora_admin.events.build_trigger.on_content_updated",
	},
	"Memora Unit": {
		"on_update": [
			"memora_admin.events.build_trigger.on_content_updated",
			"memora_admin.events.access_sync.on_unit_free_changed",
		],
		"on_trash": [
			"memora_admin.events.build_trigger.on_content_updated",
			"memora_admin.events.access_sync.on_unit_free_changed",
		],
	},
	"Memora Topic": {
		"on_update": [
			"memora_admin.events.build_trigger.on_content_updated",
			"memora_admin.events.access_sync.on_topic_free_changed",
		],
		"on_trash": [
			"memora_admin.events.build_trigger.on_content_updated",
			"memora_admin.events.access_sync.on_topic_free_changed",
		],
	},
	"Memora Lesson": {
		"on_update": [
			"memora_admin.events.build_trigger.on_content_updated",
			"memora_admin.events.review_item_sync.on_lesson_save",
		],
		"on_trash": [
			"memora_admin.events.build_trigger.on_content_updated",
			"memora_admin.events.review_item_sync.on_lesson_trash",
		],
	},
	# Plan build trigger events (debounced)
	"Memora Academic Plan": {
		"on_update": "memora_admin.events.build_trigger.on_plan_updated",
		"on_trash": "memora_admin.events.build_trigger.on_plan_deleted",
	},
	"Memora Plan Subject": {
		"after_insert": [
			"memora_admin.events.build_trigger.on_plan_subject_changed",
			"memora_admin.events.access_sync.on_plan_subject_changed",
		],
		"on_update": [
			"memora_admin.events.build_trigger.on_plan_subject_changed",
			"memora_admin.events.access_sync.on_plan_subject_changed",
		],
		"on_trash": [
			"memora_admin.events.build_trigger.on_plan_subject_changed",
			"memora_admin.events.access_sync.on_plan_subject_changed",
		],
	},
	"Memora Settings": {
		"on_update": "memora_admin.events.settings_sync.on_settings_updated",
	},
	"Memora Level Settings": {
		"on_update": "memora_admin.events.level_sync.on_level_settings_updated",
	},
	"Memora Plan Overrider": {
		"after_insert": "memora_admin.events.build_trigger.on_plan_overrider_changed",
		"on_update": "memora_admin.events.build_trigger.on_plan_overrider_changed",
		"on_trash": "memora_admin.events.build_trigger.on_plan_overrider_changed",
	},
	# Announcement cache invalidation
	"Memora Announcement": {
		"after_insert": "memora_admin.events.announcement_sync.on_announcement_changed",
		"on_update": "memora_admin.events.announcement_sync.on_announcement_changed",
		"on_trash": "memora_admin.events.announcement_sync.on_announcement_changed",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		# Every 1 minute: Sync dirty data from Redis to MariaDB + FSRS + builds
		"* * * * *": [
			"memora_admin.tasks.sync.sync_dirty_progress",
			"memora_admin.tasks.sync.sync_dirty_wallets",
			"memora_admin.tasks.sync.flush_interaction_buffer",
			"memora_admin.tasks.sync.sync_dirty_challenge_progress",
			"memora_admin.tasks.fsrs_processor.process_fsrs_reviews",
			"memora_admin.tasks.build_worker.process_pending_builds",
			"memora_admin.tasks.live_challenge_transitions.process_live_challenge_transitions",
		],
		# Every 2 minutes: Sync dirty Review Item extraction from Redis to MariaDB
		"*/2 * * * *": [
			"memora_admin.tasks.sync.sync_dirty_review_items",
		],
		# Daily at 00:05: Streak reset (after midnight Asia/Amman)
		"5 0 * * *": ["memora_admin.tasks.streak_reset.reset_broken_streaks"],
		# Hourly at :15: Session cleanup (safety net for orphaned keys)
		"15 * * * *": ["memora_admin.tasks.session_cleanup.cleanup_expired_sessions"],
		# Daily at 00:10: Daily leaderboard archive
		"10 0 * * *": ["memora_admin.tasks.leaderboard_reset.archive_daily_leaderboard"],
		# Friday at 00:15: Weekly leaderboard archive (Islamic week ends Thursday)
		"15 0 * * 5": ["memora_admin.tasks.leaderboard_reset.archive_weekly_leaderboard"],
		# Hourly at :30: Pre-warm profile cache for active leaderboard players
		"30 * * * *": ["memora_admin.tasks.profile_cache.warm_profile_cache"],
		# Every 6 hours: Sync all plan subjects to Redis (safety net)
		"0 */6 * * *": ["memora_admin.tasks.plan_sync.sync_all_plan_subjects_to_redis"],
		# Every 5 minutes: Redis health monitoring with threshold alerting
		"*/5 * * * *": ["memora_admin.tasks.redis_monitor.monitor_redis_health"],
		# Daily at 03:00: Clean up old leaderboard keys (30d daily, 90d weekly/archive)
		"0 3 * * *": ["memora_admin.tasks.leaderboard_cleanup.cleanup_old_leaderboards"],
		# Daily at 01:05: Expire cards linked to ended/unpublished seasons
		"5 1 * * *": ["memora_admin.tasks.season_expiration.expire_season_cards"],
		# Daily at 01:10: Reset Challenge Hub data for seasons past end_date
		"10 1 * * *": ["memora_admin.events.access_sync.check_expired_seasons_challenge_reset"],
		# Daily at 02:30: Delete encrypted voucher exports older than 30 days
		"30 2 * * *": ["memora_admin.tasks.voucher_cleanup.cleanup_expired_exports"],
		# Monthly on 1st at 02:00: Generate consignment invoices for previous month
		"0 2 1 * *": ["memora_admin.tasks.consignment_billing.generate_monthly_invoices"],
		# Daily at 01:00: Delete expired Memora Announcements
		"0 1 * * *": ["memora_admin.tasks.announcement_cleanup.cleanup_expired_announcements"],
	}
}

# Commented defaults for reference:
# scheduler_events = {
# 	"all": [
# 		"memora_admin.tasks.all"
# 	],
# 	"daily": [
# 		"memora_admin.tasks.daily"
# 	],
# 	"hourly": [
# 		"memora_admin.tasks.hourly"
# 	],
# 	"weekly": [
# 		"memora_admin.tasks.weekly"
# 	],
# 	"monthly": [
# 		"memora_admin.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "memora_admin.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "memora_admin.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "memora_admin.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["memora_admin.utils.before_request"]
# after_request = ["memora_admin.utils.after_request"]

# Job Events
# ----------
# before_job = ["memora_admin.utils.before_job"]
# after_job = ["memora_admin.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"memora_admin.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []
