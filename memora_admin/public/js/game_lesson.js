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
        } else if (row.stage_type === 'MINDMAP') {
            open_mindmap_dialog(frm, cdt, cdn, row, config_json);
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

// =================================================
// 🧠 4. نافذة الخريطة الذهنية (Mind Map)
// =================================================
function open_mindmap_dialog(frm, cdt, cdn, row, data) {
    let root_label = data.label || '';
    let root_description = data.description || '';

    // تحويل الشجرة المحفوظة إلى قائمة مسطحة مرتبة
    let existing_data = [];
    if (data.children && Array.isArray(data.children)) {
        data.children.forEach(branch => {
            existing_data.push({
                node_type: 'فرع',
                label: branch.label,
                description: branch.description || ''
            });
            if (branch.children && Array.isArray(branch.children)) {
                branch.children.forEach(item => {
                    existing_data.push({
                        node_type: 'عنصر',
                        label: item.label,
                        description: item.description || ''
                    });
                });
            }
        });
    }

    let d = new frappe.ui.Dialog({
        title: 'إعدادات الخريطة الذهنية (Mind Map)',
        fields: [
            {
                label: 'عنوان الخريطة (العنوان الرئيسي)',
                fieldname: 'root_label',
                fieldtype: 'Data',
                reqd: 1,
                default: root_label
            },
            {
                label: 'وصف الخريطة',
                fieldname: 'root_description',
                fieldtype: 'Small Text',
                default: root_description
            },
            {
                fieldtype: 'Section Break',
                label: 'محتوى الخريطة'
            },
            {
                label: '',
                fieldname: 'nodes_table',
                fieldtype: 'Table',
                cannot_add_rows: false,
                description: 'أضف "فرع" للفروع الرئيسية، و"عنصر" للتفاصيل تحت كل فرع. كل عنصر ينتمي للفرع الذي يسبقه في القائمة.',
                fields: [
                    {
                        label: 'النوع',
                        fieldname: 'node_type',
                        fieldtype: 'Select',
                        options: 'فرع\nعنصر',
                        in_list_view: 1,
                        reqd: 1,
                        columns: 2
                    },
                    {
                        label: 'العنوان',
                        fieldname: 'label',
                        fieldtype: 'Data',
                        in_list_view: 1,
                        reqd: 1,
                        columns: 4
                    },
                    {
                        label: 'الوصف',
                        fieldname: 'description',
                        fieldtype: 'Data',
                        in_list_view: 1,
                        columns: 4
                    }
                ],
                data: existing_data,
                get_data: () => existing_data
            }
        ],
        size: 'extra-large',
        primary_action_label: 'حفظ (Save)',
        primary_action: function(values) {
            let children = [];
            let current_branch = null;
            let used_ids = new Set();

            // التحقق: أول صف يجب أن يكون "فرع"
            if (values.nodes_table.length > 0 && values.nodes_table[0].node_type !== 'فرع') {
                frappe.msgprint('يجب أن يكون الصف الأول من نوع "فرع". لا يمكن إضافة عنصر بدون فرع يسبقه.');
                return;
            }

            for (let node of values.nodes_table) {
                let id = _generate_mindmap_id(used_ids);
                used_ids.add(id);

                if (node.node_type === 'فرع') {
                    current_branch = {
                        id: id,
                        label: node.label,
                        children: []
                    };
                    if (node.description) current_branch.description = node.description;
                    children.push(current_branch);
                } else {
                    // عنصر فرعي — ينتمي للفرع الحالي
                    if (!current_branch) {
                        frappe.msgprint('لا يمكن إضافة عنصر بدون فرع يسبقه.');
                        return;
                    }
                    let item = {
                        id: id,
                        label: node.label
                    };
                    if (node.description) item.description = node.description;
                    current_branch.children.push(item);
                }
            }

            if (children.length === 0) {
                frappe.msgprint('يجب إضافة فرع واحد على الأقل.');
                return;
            }

            let config_payload = {
                label: values.root_label,
                children: children
            };
            if (values.root_description) {
                config_payload.description = values.root_description;
            }

            frappe.model.set_value(cdt, cdn, 'config_json', JSON.stringify(config_payload, null, 2));
            d.hide();
            frappe.show_alert({message: 'تم حفظ الخريطة الذهنية', indicator: 'green'});
        }
    });

    d.show();
}

function _generate_mindmap_id(used_ids) {
    const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
    let id;
    do {
        id = '';
        for (let i = 0; i < 3; i++) {
            id += chars.charAt(Math.floor(Math.random() * chars.length));
        }
    } while (used_ids.has(id));
    return id;
}