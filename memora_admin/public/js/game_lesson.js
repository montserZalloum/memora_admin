frappe.ui.form.on('Memora Lesson', {
    refresh: function(frm) {
        // 
    }
});

frappe.ui.form.on('Memora Lesson Stage', {
    edit_content_btn: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        
        if (!row.stage_type) {
            frappe.msgprint("الرجاء اختيار نوع المرحلة أولاً");
            return;
        }

        let config_json = {};
        if (row.config_json) {
            try {
                config_json = JSON.parse(row.config_json);
            } catch (e) {
                console.error("Invalid JSON", e);
            }
        }

        if (row.stage_type === 'MATCHING') {
            open_matching_dialog(frm, cdt, cdn, row, config_json);
        } else if (row.stage_type === 'REVEAL') {
            open_reveal_dialog(frm, cdt, cdn, row, config_json);
        } else if (row.stage_type === 'SENTENCE_BUILDER') {
            open_sentence_builder_dialog(frm, cdt, cdn, row, config_json);
        } else {
            frappe.msgprint("لا يوجد محرر لهذا النوع بعد");
        }
    }
});

// =================================================
// 🧩 1. نافذة إعدادات التوصيل (Matching)
// =================================================
function open_matching_dialog(frm, cdt, cdn, row, data) {
    let existing_data = (data.pairs || []).map(p => ({
        item_1: p.right,
        item_2: p.left
    }));

    let d = new frappe.ui.Dialog({
        title: 'إعدادات التوصيل (Matching)',
        fields: [
            {
                label: 'التعليمات',
                fieldname: 'instruction',
                fieldtype: 'Data',
                default: data.instruction || 'طابق العناصر'
            },
            {
                label: 'الأزواج',
                fieldname: 'pairs_table',
                fieldtype: 'Table',
                cannot_add_rows: false,
                // 👇 الحل السحري: تعريف الحقول يدوياً هنا
                fields: [
                    {
                        label: 'اليمين (Right)', // نحدد الاسم هنا مباشرة
                        fieldname: 'item_1',
                        fieldtype: 'Data',
                        in_list_view: 1,
                        reqd: 1
                    },
                    {
                        label: 'اليسار (Left)',
                        fieldname: 'item_2',
                        fieldtype: 'Data',
                        in_list_view: 1,
                        reqd: 1
                    }
                ],
                data: existing_data,
                get_data: () => existing_data
            }
        ],
        size: 'large',
        primary_action_label: 'حفظ (Save)',
        primary_action: function(values) {
            let config_payload = {
                instruction: values.instruction,
                pairs: values.pairs_table.map((p, index) => ({
                    id: String(index + 1),
                    right: p.item_1,
                    left: p.item_2
                }))
            };
            frappe.model.set_value(cdt, cdn, 'config_json', JSON.stringify(config_payload, null, 2));
            d.hide();
            frappe.show_alert({message: 'تم الحفظ ✅', indicator: 'green'});
        }
    });

    d.show();
}

// =================================================
// 🔍 2. نافذة إعدادات الكشف (Reveal)
// =================================================
function open_reveal_dialog(frm, cdt, cdn, row, data) {
    let existing_data = (data.highlights || []).map(h => ({
        item_1: h.word,
        item_2: h.explanation
    }));

    let d = new frappe.ui.Dialog({
        title: 'إعدادات الكشف (Reveal)',
        fields: [
            {
                label: 'الأيقونة (Emoji)',
                fieldname: 'image',
                fieldtype: 'Data',
                default: data.image
            },
            {
                label: 'الجملة',
                fieldname: 'sentence',
                fieldtype: 'Small Text',
                reqd: 1,
                default: data.sentence
            },
            {
                label: 'الكلمات',
                fieldname: 'highlights_table',
                fieldtype: 'Table',
                cannot_add_rows: false,
                // 👇 تعريف الحقول يدوياً هنا أيضاً
                fields: [
                    {
                        label: 'الكلمة (Word)',
                        fieldname: 'item_1',
                        fieldtype: 'Data',
                        in_list_view: 1,
                        reqd: 1
                    },
                    {
                        label: 'الشرح (Explanation)',
                        fieldname: 'item_2',
                        fieldtype: 'Data',
                        in_list_view: 1
                    }
                ],
                data: existing_data,
                get_data: () => existing_data
            }
        ],
        size: 'large',
        primary_action_label: 'حفظ (Save)',
        primary_action: function(values) {
            let config_payload = {
                image: values.image,
                sentence: values.sentence,
                highlights: values.highlights_table.map(h => ({
                    word: h.item_1,
                    explanation: h.item_2
                }))
            };
            frappe.model.set_value(cdt, cdn, 'config_json', JSON.stringify(config_payload, null, 2));
            d.hide();
            frappe.show_alert({message: 'تم الحفظ', indicator: 'green'});
        }
    });

    d.show();
}

// =================================================
// 🏗️ 3. نافذة بناء الجملة (Sentence Builder)
// =================================================
function open_sentence_builder_dialog(frm, cdt, cdn, row, data) {
    // تجهيز البيانات القديمة إذا كانت موجودة
    let existing_data = (data.words || []).map(w => ({
        item_1: w
    }));

    let d = new frappe.ui.Dialog({
        title: 'إعدادات بناء الجملة (Sentence Builder)',
        fields: [
            {
                label: 'التعليمات',
                fieldname: 'instruction',
                fieldtype: 'Data',
                default: data.instruction || 'رتب الكلمات لتكوين جملة صحيحة',
                description: 'مثال: رتب الكلمات التالية'
            },
            {
                fieldtype: 'Section Break',
                label: 'محتوى الجملة'
            },
            {
                label: 'الجملة الكاملة (للمراجعة)',
                fieldname: 'sentence',
                fieldtype: 'Small Text',
                default: data.sentence,
                description: 'اكتب الجملة كاملة هنا كمرجع'
            },
            {
                label: 'الكلمات/المقاطع مرتبة (Words Tokens)',
                fieldname: 'words_table',
                fieldtype: 'Table',
                cannot_add_rows: false,
                description: 'أضف الكلمات بالترتيب الصحيح. ملاحظة: يمكنك إضافة عبارة كاملة في سطر واحد لتظهر كزر واحد (مثل: حق إصدار العملة)',
                fields: [
                    {
                        label: 'الكلمة / العبارة',
                        fieldname: 'item_1',
                        fieldtype: 'Data',
                        in_list_view: 1,
                        reqd: 1
                    }
                ],
                data: existing_data
            }
        ],
        size: 'large',
        primary_action_label: 'حفظ (Save)',
        primary_action: function(values) {
            // تحويل الجدول إلى مصفوفة نصوص بسيطة للـ React
            let words_array = values.words_table.map(row => row.item_1);

            let config_payload = {
                instruction: values.instruction,
                sentence: values.sentence,
                words: words_array // سيتم إرسالها كـ Array من الكلمات
            };

            // حفظ الـ JSON في حقل الـ Config
            frappe.model.set_value(cdt, cdn, 'config_json', JSON.stringify(config_payload, null, 2));

            d.hide();
            frappe.show_alert({message: 'تم حفظ إعدادات الجملة', indicator: 'green'});
        }
    });

    d.show();
}