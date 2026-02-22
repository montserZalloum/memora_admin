frappe.ui.form.on("Memora Level Settings", {
	refresh(frm) {
		// Show XP preview for first few levels when curve params are set
		if (frm.doc.quadratic_coefficient && frm.doc.linear_coefficient) {
			let a = frm.doc.quadratic_coefficient;
			let b = frm.doc.linear_coefficient;
			let max = frm.doc.max_level || 15;
			let preview = [];
			for (let lvl = 1; lvl <= Math.min(max, 20); lvl++) {
				let threshold = Math.round(a * Math.pow(lvl - 1, 2) + b * (lvl - 1));
				preview.push(`Level ${lvl}: ${threshold.toLocaleString()} XP`);
			}
			frm.set_intro(preview.join(" | "), "blue");
		}
	},
	after_save(frm) {
		frm.reload_doc();
	},
});
