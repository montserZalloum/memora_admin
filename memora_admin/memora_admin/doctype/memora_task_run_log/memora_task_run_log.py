# Copyright (c) 2026, corex and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MemoraTaskRunLog(Document):
	"""Log entry for scheduled task execution.

	Tracks task runs with timing, status, and error details for observability.
	Used by task_utils.log_task_run() for consistent logging across all tasks.
	"""


	def autoname(self):
		from frappe.model.naming import make_autoname

		self.name = make_autoname("TASK-.#####.")
	pass
