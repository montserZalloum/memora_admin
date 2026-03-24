# Memora Admin DocTypes ASCII Tree

Generated from `memora_admin/memora_admin/doctype/*/*.json`.

- Top-level DocTypes: 46
- Child Table DocTypes: 15
- Declared fields: 698

```text
Memora Admin DocTypes
|-- Memora Academic Plan [DocType, 13 fields]
|   |-- plan_name [Data]
|   |-- grade [Link] -> Memora Grade
|   |-- major [Link] -> Memora Major
|   |-- season [Link] -> Memora Season
|   |-- is_published [Check]
|   |-- plan_subjects [Table] -> Memora Plan Subject
|   |   `-- Memora Plan Subject [Child DocType, 5 fields]
|   |       |-- subject [Link] -> Memora Subject
|   |       |-- alias_title [Data]
|   |       |-- notes [Small Text]
|   |       |-- meta_data [JSON]
|   |       `-- is_premium [Check]
|   |-- sb_stats [Section Break]
|   |-- total_subjects [Int]
|   |-- total_lessons [Int]
|   |-- sb_json [Section Break]
|   |-- json_version [Int]
|   |-- json_hash [Data]
|   `-- json_generated_at [Datetime]
|-- Memora Achievement [DocType, 11 fields]
|   |-- achievement_title [Data]
|   |-- description [Small Text]
|   |-- badge_image [Attach Image]
|   |-- sb_unlock [Section Break]
|   |-- achievement_type [Select]
|   |-- threshold [Int]
|   |-- subject [Link] -> Memora Subject
|   |-- sb_rewards [Section Break]
|   |-- xp_reward [Int]
|   |-- is_active [Check]
|   `-- sort_order [Int]
|-- Memora Admin Filter [DocType, 17 fields]
|   |-- filter_name [Data]
|   |-- sb_plan_context [Section Break]
|   |-- season [Link] -> Memora Season
|   |-- grade [Link] -> Memora Grade
|   |-- cb_plan_context [Column Break]
|   |-- major [Link] -> Memora Major
|   |-- academic_plan [Link] -> Memora Academic Plan
|   |-- sb_content_scope [Section Break]
|   |-- subject [Link] -> Memora Subject
|   |-- track [Link] -> Memora Track
|   |-- cb_content_scope [Column Break]
|   |-- unit [Link] -> Memora Unit
|   |-- topic [Link] -> Memora Topic
|   |-- sb_test_filter [Section Break]
|   |-- test_level [Select]
|   |-- test_filter_btn [Button]
|   `-- test_results_html [HTML]
|-- Memora Analytics Aggregate [DocType, 5 fields]
|   |-- lesson [Link] -> Memora Lesson
|   |-- date [Date]
|   |-- total_attempts [Int]
|   |-- avg_time_spent [Float]
|   `-- success_rate [Float]
|-- Memora Announcement [DocType, 19 fields]
|   |-- title_ar [Data]
|   |-- title_en [Data]
|   |-- column_break_content [Column Break]
|   |-- body_ar [Text Editor]
|   |-- body_en [Text Editor]
|   |-- section_break_targeting [Section Break]
|   |-- target_audience [Select]
|   |-- target_plans [Table] -> Memora Announcement Target Plan
|   |   `-- Memora Announcement Target Plan [Child DocType, 1 fields]
|   |       `-- plan [Link] -> Memora Academic Plan
|   |-- section_break_duration [Section Break]
|   |-- duration_type [Select]
|   |-- start_date [Date]
|   |-- end_date [Date]
|   |-- duration_days [Int]
|   |-- column_break_effective [Column Break]
|   |-- effective_start_date [Date]
|   |-- effective_end_date [Date]
|   |-- section_break_display [Section Break]
|   |-- display_frequency [Select]
|   `-- is_published [Check]
|-- Memora Archive Job [DocType, 41 fields]
|   |-- source_doctype [Data]
|   |-- archive_scope [Data]
|   |-- schema_version [Data]
|   |-- column_break_identity [Column Break]
|   |-- archive_type [Data]
|   |-- status [Select]
|   |-- priority [Select]
|   |-- section_execution [Section Break]
|   |-- execution_stage [Data]
|   |-- started_at [Datetime]
|   |-- claimed_at [Datetime]
|   |-- exported_at [Datetime]
|   |-- column_break_execution [Column Break]
|   |-- completed_at [Datetime]
|   |-- snapshot_taken_at [Datetime]
|   |-- transferred_at [Datetime]
|   |-- ingested_at [Datetime]
|   |-- duration_seconds [Float]
|   |-- section_output [Section Break]
|   |-- row_count [Int]
|   |-- file_path [Data]
|   |-- column_break_output [Column Break]
|   |-- file_checksum [Data]
|   |-- file_size_bytes [Int]
|   |-- remote_path [Data]
|   |-- section_retry [Section Break]
|   |-- retry_count [Int]
|   |-- retry_btn [Button]
|   |-- column_break_retry [Column Break]
|   |-- error_log [Long Text]
|   |-- notified_at [Datetime]
|   |-- section_behavior [Section Break]
|   |-- post_archive_action [Select]
|   |-- source_deleted [Check]
|   |-- purge_progress [JSON]
|   |-- column_break_behavior [Column Break]
|   |-- sync_paused [Check]
|   |-- sync_paused_at [Datetime]
|   |-- clear_pause_btn [Button]
|   |-- section_meta [Section Break]
|   `-- job_meta [JSON]
|-- Memora Build Queue [DocType, 13 fields]
|   |-- target_type [Select]
|   |-- target_name [Dynamic Link] -> target_type
|   |-- trigger_reason [Select]
|   |-- triggered_by [Link] -> User
|   |-- triggered_at [Datetime]
|   |-- status_section [Section Break]
|   |-- status [Select]
|   |-- started_at [Datetime]
|   |-- completed_at [Datetime]
|   |-- duration_sec [Float]
|   |-- results_section [Section Break]
|   |-- files_generated [Int]
|   `-- error_message [Text]
|-- Memora Challenge Attempt [DocType, 14 fields]
|   |-- naming_series [Select]
|   |-- player [Link] -> Memora Player Profile
|   |-- topic [Link] -> Memora Topic
|   |-- subject [Link] -> Memora Subject
|   |-- season [Link] -> Memora Season
|   |-- attempt_number [Int]
|   |-- total_questions [Int]
|   |-- correct_count [Int]
|   |-- score_pct [Percent]
|   |-- passed [Check]
|   |-- time_spent [Int]
|   |-- xp_earned [Int]
|   |-- submitted_at [Datetime]
|   `-- details [Table] -> Memora Challenge Attempt Detail
|       `-- Memora Challenge Attempt Detail [Child DocType, 4 fields]
|           |-- item_id [Data]
|           |-- correct [Check]
|           |-- time_spent [Int]
|           `-- chosen_answer [Int]
|-- Memora Challenge Progress [DocType, 10 fields]
|   |-- player [Link] -> Memora Player Profile
|   |-- topic [Link] -> Memora Topic
|   |-- subject [Link] -> Memora Subject
|   |-- season [Link] -> Memora Season
|   |-- stamped [Check]
|   |-- best_correct [Int]
|   |-- best_score_pct [Percent]
|   |-- best_passing_pct [Percent]
|   |-- total_xp_earned [Int]
|   `-- attempt_count [Int]
|-- Memora Content Report [DocType, 7 fields]
|   |-- player [Link] -> Memora Player Profile
|   |-- subject [Link] -> Memora Subject
|   |-- lesson [Link] -> Memora Lesson
|   |-- screen_shot [Attach Image]
|   |-- report_type [Select]
|   |-- description [Small Text]
|   `-- status [Select]
|-- Memora Grade [DocType, 3 fields]
|   |-- grade_title [Data]
|   |-- sort_order [Int]
|   `-- majors [Table] -> Memora Grade Major
|       `-- Memora Grade Major [Child DocType, 1 fields]
|           `-- major [Link] -> Memora Major
|-- Memora Interaction Log [DocType, 9 fields]
|   |-- player [Link] -> Memora Player Profile
|   |-- lesson [Link] -> Memora Lesson
|   |-- stage_id [Data]
|   |-- item_id [Data]
|   |-- event_type [Select]
|   |-- time_spent [Int]
|   |-- errors_count [Int]
|   |-- timestamp [Datetime]
|   `-- client_metadata [Code]
|-- Memora Lesson [DocType, 16 fields]
|   |-- filter_section [Section Break]
|   |-- admin_filter_html [HTML]
|   |-- main_section [Section Break]
|   |-- lesson_title [Data]
|   |-- topic [Link] -> Memora Topic
|   |-- base_xp [Int]
|   |-- is_reviewable [Check]
|   |-- max_hearts [Int]
|   |-- is_published [Check]
|   |-- content_hash [Data]
|   |-- stages [Table] -> Memora Lesson Stage
|   |   `-- Memora Lesson Stage [Child DocType, 5 fields]
|   |       |-- stage_title [Data]
|   |       |-- stage_type [Link] -> Memora Lesson Stage Settings
|   |       |-- is_skippable [Check]
|   |       |-- config_json [Code]
|   |       `-- edit_content_btn [Button]
|   |-- bit_index [Int]
|   |-- sb_hierarchy [Section Break]
|   |-- unit [Link] -> Memora Unit
|   |-- track [Link] -> Memora Track
|   `-- subject [Link] -> Memora Subject
|-- Memora Lesson Stage Settings [DocType, 3 fields]
|   |-- stage_title [Data]
|   |-- is_skippable [Check]
|   `-- payload [Code]
|-- Memora Level Settings [DocType, 7 fields]
|   |-- curve_section [Section Break]
|   |-- quadratic_coefficient [Int]
|   |-- linear_coefficient [Int]
|   |-- column_break_curve [Column Break]
|   |-- max_level [Int]
|   |-- titles_section [Section Break]
|   `-- level_titles [Table] -> Memora Level Title
|       `-- Memora Level Title [Child DocType, 4 fields]
|           |-- level_number [Int]
|           |-- title_en [Data]
|           |-- title_ar [Data]
|           `-- icon [Attach Image]
|-- Memora Live Challenge Event [DocType, 43 fields]
|   |-- event_name [Data]
|   |-- status [Select]
|   |-- column_break_basic [Column Break]
|   |-- description [Text Editor]
|   |-- section_break_mode [Section Break]
|   |-- mode [Select]
|   |-- starting_hearts [Int]
|   |-- result_window_duration [Int]
|   |-- section_break_schedule [Section Break]
|   |-- scheduled_start [Datetime]
|   |-- waiting_room_duration [Int]
|   |-- column_break_schedule [Column Break]
|   |-- exam_duration [Int]
|   |-- capacity [Int]
|   |-- section_break_computed [Section Break]
|   |-- exam_start_ts [Datetime]
|   |-- column_break_computed [Column Break]
|   |-- exam_end_ts [Datetime]
|   |-- section_break_timer [Section Break]
|   |-- enable_question_timer [Check]
|   |-- question_time_limit [Int]
|   |-- section_break_settings [Section Break]
|   |-- is_paid [Check]
|   |-- price [Currency]
|   |-- currency [Link] -> Currency
|   |-- column_break_settings [Column Break]
|   |-- section_break_xp [Section Break]
|   |-- participation_xp [Int]
|   |-- first_place_xp [Int]
|   |-- column_break_xp [Column Break]
|   |-- second_place_xp [Int]
|   |-- third_place_xp [Int]
|   |-- column_break_xp2 [Column Break]
|   |-- default_xp [Int]
|   |-- section_break_questions [Section Break]
|   |-- questions [Table] -> Memora Live Challenge Question
|   |   `-- Memora Live Challenge Question [Child DocType, 7 fields]
|   |       |-- question_text [Small Text]
|   |       |-- option_a [Data]
|   |       |-- option_b [Data]
|   |       |-- option_c [Data]
|   |       |-- option_d [Data]
|   |       |-- correct_answer [Select]
|   |       `-- source_review_item [Link] -> Memora Review Item
|   |-- section_break_plans [Section Break]
|   |-- eligible_plans [Table] -> Memora Live Challenge Eligible Plan
|   |   `-- Memora Live Challenge Eligible Plan [Child DocType, 1 fields]
|   |       `-- plan [Link] -> Memora Academic Plan
|   |-- section_break_stats [Section Break]
|   |-- participant_count [Int]
|   |-- submitted_count [Int]
|   |-- column_break_stats [Column Break]
|   `-- leaderboard_json [JSON]
|-- Memora Live Challenge Participation [DocType, 18 fields]
|   |-- event [Link] -> Memora Live Challenge Event
|   |-- player [Link] -> Memora Player Profile
|   |-- column_break_main [Column Break]
|   |-- joined_at [Datetime]
|   |-- submitted_at [Datetime]
|   |-- section_break_results [Section Break]
|   |-- score [Float]
|   |-- rank [Int]
|   |-- column_break_results [Column Break]
|   |-- xp_awarded [Int]
|   |-- section_break_detail [Section Break]
|   |-- answers_json [JSON]
|   |-- section_break_last_stand [Section Break]
|   |-- final_hearts [Int]
|   |-- is_eliminated [Check]
|   |-- column_break_last_stand [Column Break]
|   |-- eliminated_at_question [Int]
|   `-- avg_response_time_ms [Int]
|-- Memora Live Event Access [DocType, 15 fields]
|   |-- section_main [Section Break]
|   |-- player [Link] -> Memora Player Profile
|   |-- event [Link] -> Memora Live Challenge Event
|   |-- column_break_main [Column Break]
|   |-- status [Select]
|   |-- access_type [Select]
|   |-- section_source [Section Break]
|   |-- purchase_ref [Link] -> Memora Live Event Purchase
|   |-- voucher_ref [Link] -> Memora Voucher Card
|   |-- column_break_source [Column Break]
|   |-- granted_by [Link] -> User
|   |-- section_revocation [Section Break]
|   |-- revoked_at [Datetime]
|   |-- column_break_revocation [Column Break]
|   `-- revoked_by [Link] -> User
|-- Memora Live Event Purchase [DocType, 23 fields]
|   |-- section_main [Section Break]
|   |-- player [Link] -> Memora Player Profile
|   |-- event [Link] -> Memora Live Challenge Event
|   |-- column_break_main [Column Break]
|   |-- plan_snapshot [Link] -> Memora Academic Plan
|   |-- season [Link] -> Memora Season
|   |-- status [Select]
|   |-- expires_at [Datetime]
|   |-- section_payment [Section Break]
|   |-- amount [Currency]
|   |-- currency [Link] -> Currency
|   |-- column_break_payment [Column Break]
|   |-- erpnext_invoice [Link] -> Sales Invoice
|   |-- section_gateway [Section Break]
|   |-- payment_gateway [Data]
|   |-- column_break_gateway [Column Break]
|   |-- payment_reference [Data]
|   |-- section_timestamps [Section Break]
|   |-- paid_at [Datetime]
|   |-- column_break_timestamps [Column Break]
|   |-- refunded_at [Datetime]
|   |-- section_references [Section Break]
|   `-- event_access_ref [Link] -> Memora Live Event Access
|-- Memora Live Sync Job [DocType, 27 fields]
|   |-- sync_type [Data]
|   |-- schema_version [Data]
|   |-- column_break_identity [Column Break]
|   |-- status [Select]
|   |-- triggered_by [Select]
|   |-- section_execution [Section Break]
|   |-- execution_stage [Data]
|   |-- started_at [Datetime]
|   |-- exported_at [Datetime]
|   |-- column_break_execution [Column Break]
|   |-- completed_at [Datetime]
|   |-- transferred_at [Datetime]
|   |-- ingested_at [Datetime]
|   |-- duration_seconds [Float]
|   |-- section_output [Section Break]
|   |-- row_count [Int]
|   |-- file_path [Data]
|   |-- column_break_output [Column Break]
|   |-- file_checksum [Data]
|   |-- file_size_bytes [Int]
|   |-- remote_path [Data]
|   |-- section_retry [Section Break]
|   |-- retry_count [Int]
|   |-- column_break_retry [Column Break]
|   |-- error_log [Long Text]
|   |-- section_meta [Section Break]
|   `-- job_meta [JSON]
|-- Memora Major [DocType, 1 fields]
|   `-- major_title [Data]
|-- Memora Memory State [DocType, 12 fields]
|   |-- season_seq [Int]
|   |-- subject [Link] -> Memora Subject
|   |-- player [Link] -> Memora Player Profile
|   |-- item_id [Data]
|   |-- stage_id [Data]
|   |-- stability [Float]
|   |-- difficulty [Float]
|   |-- next_review [Date]
|   |-- lesson [Link] -> Memora Lesson
|   |-- state [Int]
|   |-- step [Int]
|   `-- last_review [Datetime]
|-- Memora Plan Overrider [DocType, 4 fields]
|   |-- plan [Link] -> Memora Academic Plan
|   |-- ref_doctype [Link] -> DocType
|   |-- ref_name [Dynamic Link] -> ref_doctype
|   `-- action [Select]
|-- Memora Plan Premium [DocType, 15 fields]
|   |-- player [Link] -> Memora Player Profile
|   |-- plan [Link] -> Memora Academic Plan
|   |-- column_break_basic [Column Break]
|   |-- season [Link] -> Memora Season
|   |-- status [Select]
|   |-- section_break_source [Section Break]
|   |-- source_type [Select]
|   |-- column_break_source [Column Break]
|   |-- purchase_ref [Link] -> Memora Plan Premium Purchase
|   |-- voucher_ref [Link] -> Memora Voucher Card
|   |-- granted_by [Link] -> User
|   |-- section_break_revocation [Section Break]
|   |-- revoked_at [Datetime]
|   |-- column_break_revocation [Column Break]
|   `-- revoked_by [Link] -> User
|-- Memora Plan Premium Purchase [DocType, 22 fields]
|   |-- section_main [Section Break]
|   |-- player [Link] -> Memora Player Profile
|   |-- plan [Link] -> Memora Academic Plan
|   |-- column_break_main [Column Break]
|   |-- season [Link] -> Memora Season
|   |-- status [Select]
|   |-- section_payment [Section Break]
|   |-- amount [Currency]
|   |-- currency [Link] -> Currency
|   |-- column_break_payment [Column Break]
|   |-- erpnext_item_code [Link] -> Item
|   |-- erpnext_invoice [Link] -> Sales Invoice
|   |-- section_gateway [Section Break]
|   |-- payment_gateway [Data]
|   |-- column_break_gateway [Column Break]
|   |-- payment_reference [Data]
|   |-- section_timestamps [Section Break]
|   |-- paid_at [Datetime]
|   |-- column_break_timestamps [Column Break]
|   |-- refunded_at [Datetime]
|   |-- section_references [Section Break]
|   `-- premium_ref [Link] -> Memora Plan Premium
|-- Memora Player Plan History [DocType, 21 fields]
|   |-- player [Link] -> Memora Player Profile
|   |-- trigger_reason [Select]
|   |-- changed_at [Datetime]
|   |-- sb_previous [Section Break]
|   |-- previous_plan [Link] -> Memora Academic Plan
|   |-- previous_grade [Link] -> Memora Grade
|   |-- previous_major [Link] -> Memora Major
|   |-- previous_season [Link] -> Memora Season
|   |-- sb_new [Section Break]
|   |-- new_plan [Link] -> Memora Academic Plan
|   |-- new_grade [Link] -> Memora Grade
|   |-- new_major [Link] -> Memora Major
|   |-- new_season [Link] -> Memora Season
|   |-- sb_snapshot [Section Break]
|   |-- snapshot_total_xp [Int]
|   |-- snapshot_current_streak [Int]
|   |-- snapshot_total_lessons [Int]
|   |-- snapshot_total_time_min [Int]
|   |-- snapshot_subscriptions_json [Long Text]
|   |-- snapshot_progress_json [Long Text]
|   `-- snapshot_memory_states [Int]
|-- Memora Player Profile [DocType, 12 fields]
|   |-- mobile [Data]
|   |-- password [Password]
|   |-- display_name [Data]
|   |-- gender [Select]
|   |-- plan [Link] -> Memora Academic Plan
|   |-- avatar [Data]
|   |-- grade [Link] -> Memora Grade
|   |-- major [Link] -> Memora Major
|   |-- season [Link] -> Memora Season
|   |-- preferred_lang [Select]
|   |-- notifications [Check]
|   `-- authorized_devices [Table] -> Memora Player Device
|       `-- Memora Player Device [Child DocType, 6 fields]
|           |-- device_id [Data]
|           |-- device_name [Data]
|           |-- last_login [Datetime]
|           |-- user_agent [Small Text]
|           |-- platform [Select]
|           `-- push_token [Text]
|-- Memora Player Subscription [DocType, 4 fields]
|   |-- player [Link] -> Memora Player Profile
|   |-- access_key [Data]
|   |-- expires_at [Date]
|   `-- is_active [Check]
|-- Memora Player Wallet [DocType, 10 fields]
|   |-- player [Link] -> Memora Player Profile
|   |-- total_xp [Int]
|   |-- current_streak [Int]
|   |-- dirty_flag [Check]
|   |-- status [Select]
|   |-- sb_stats [Section Break]
|   |-- total_lessons [Int]
|   |-- total_time_min [Int]
|   |-- last_sync_at [Datetime]
|   `-- daily_xp_json [Small Text]
|-- Memora Product Grant [DocType, 6 fields]
|   |-- grade [Link] -> Memora Grade
|   |-- major [Link] -> Memora Major
|   |-- plan [Link] -> Memora Academic Plan
|   |-- item_code [Link] -> Item
|   |-- is_published [Check]
|   `-- grant_components [Table] -> Memora Grant Component
|       `-- Memora Grant Component [Child DocType, 2 fields]
|           |-- target_doctype [Select]
|           `-- target_name [Dynamic Link] -> target_doctype
|-- Memora Review Item [DocType, 18 fields]
|   |-- item_id [Data]
|   |-- hierarchy_section [Section Break]
|   |-- subject [Link] -> Memora Subject
|   |-- track [Link] -> Memora Track
|   |-- unit [Link] -> Memora Unit
|   |-- topic [Link] -> Memora Topic
|   |-- lesson [Link] -> Memora Lesson
|   |-- stage_section [Section Break]
|   |-- stage_id [Data]
|   |-- stage_type [Link] -> Memora Lesson Stage Settings
|   |-- question_section [Section Break]
|   |-- question_text [Small Text]
|   |-- choice_1 [Small Text]
|   |-- choice_2 [Small Text]
|   |-- choice_3 [Small Text]
|   |-- choice_4 [Small Text]
|   |-- correct_choice [Int]
|   `-- content_json [Code]
|-- Memora Season [DocType, 5 fields]
|   |-- season_title [Data]
|   |-- season_seq [Int]
|   |-- start_date [Date]
|   |-- end_date [Date]
|   `-- is_published [Check]
|-- Memora Settings [DocType, 35 fields]
|   |-- cdn_section [Section Break]
|   |-- cdn_enabled [Check]
|   |-- cdn_base_url [Data]
|   |-- storage_provider [Select]
|   |-- cloudflare_zone_id [Data]
|   |-- column_break_cdn [Column Break]
|   |-- access_key [Password]
|   |-- gamification_section [Section Break]
|   |-- default_max_hearts [Int]
|   |-- xp_per_heart [Int]
|   |-- column_break_game [Column Break]
|   |-- base_lesson_xp [Int]
|   |-- replay_xp [Int]
|   |-- max_streak_multiplier_percent [Int]
|   |-- security_section [Section Break]
|   |-- max_devices_per_player [Int]
|   |-- session_timeout_days [Int]
|   |-- fsrs_section [Section Break]
|   |-- review_session_size [Int]
|   |-- practice_section [Section Break]
|   |-- practice_session_size [Int]
|   |-- column_break_practice [Column Break]
|   |-- practice_session_ttl [Int]
|   |-- challenge_section [Section Break]
|   |-- challenge_xp_per_question [Int]
|   |-- challenge_pass_threshold [Int]
|   |-- column_break_challenge [Column Break]
|   |-- challenge_lb_top_count [Int]
|   |-- challenge_lb_refresh_interval [Int]
|   |-- analytics_section [Section Break]
|   |-- analytics_ssh_host [Data]
|   |-- analytics_ssh_user [Data]
|   |-- column_break_analytics [Column Break]
|   |-- analytics_ssh_key_path [Data]
|   `-- analytics_remote_path [Data]
|-- Memora Structure Progress [DocType, 4 fields]
|   |-- player [Link] -> Memora Player Profile
|   |-- subject [Link] -> Memora Subject
|   |-- passed_lessons_bitset [Long Text]
|   `-- completion_percentage [Float]
|-- Memora Subject [DocType, 17 fields]
|   |-- subject_title [Data]
|   |-- language [Select]
|   |-- image [Attach Image]
|   |-- description [Text Editor]
|   |-- in_linear [Check]
|   |-- is_published [Check]
|   |-- last_bit_index [Int]
|   |-- sort_order [Int]
|   |-- sb_applicability [Section Break]
|   |-- applicable_to [Table] -> Memora Subject Applicability
|   |   `-- Memora Subject Applicability [Child DocType, 2 fields]
|   |       |-- grade [Link] -> Memora Grade
|   |       `-- major [Link] -> Memora Major
|   |-- json_generated_at [Datetime]
|   |-- sb_stats [Section Break]
|   |-- total_tracks [Int]
|   |-- total_lessons [Int]
|   |-- sb_json [Section Break]
|   |-- json_hash [Data]
|   `-- cdn_url [Data]
|-- Memora Subscription Transaction [DocType, 8 fields]
|   |-- player [Link] -> Memora Player Profile
|   |-- payment_method [Select]
|   |-- status [Select]
|   |-- transaction_id [Data]
|   |-- amount_paid [Currency]
|   |-- payment_proof [Attach Image]
|   |-- erpnext_invoice [Link] -> Sales Invoice
|   `-- related_grant [Link] -> Memora Product Grant
|-- Memora Sync Log [DocType, 4 fields]
|   |-- job_id [Data]
|   |-- sync_type [Select]
|   |-- records_processed [Int]
|   `-- status [Select]
|-- Memora Task Log Archive Batch [DocType, 14 fields]
|   |-- source_doctype [Data]
|   |-- date_from [Date]
|   |-- date_to [Date]
|   |-- cutoff_date [Date]
|   |-- row_count [Int]
|   |-- file_path [Data]
|   |-- file_checksum [Data]
|   |-- status [Select]
|   |-- exported_at [Datetime]
|   |-- synced_at [Datetime]
|   |-- purged_at [Datetime]
|   |-- last_error [Text]
|   |-- retry_count [Int]
|   `-- archive_job_id [Data]
|-- Memora Task Run Log [DocType, 13 fields]
|   |-- task_name [Data]
|   |-- run_date [Date]
|   |-- started_at [Datetime]
|   |-- completed_at [Datetime]
|   |-- duration_sec [Float]
|   |-- column_break_status [Column Break]
|   |-- status [Select]
|   |-- triggered_by [Select]
|   |-- processed_count [Int]
|   |-- failed_count [Int]
|   |-- section_break_errors [Section Break]
|   |-- error_message [Text]
|   `-- failed_details [Code]
|-- Memora Topic [DocType, 13 fields]
|   |-- filter_section [Section Break]
|   |-- admin_filter_html [HTML]
|   |-- main_section [Section Break]
|   |-- topic_title [Data]
|   |-- unit [Link] -> Memora Unit
|   |-- sort_order [Int]
|   |-- is_free [Check]
|   |-- is_linear [Check]
|   |-- is_published [Check]
|   |-- sb_hierarchy [Section Break]
|   |-- track [Link] -> Memora Track
|   |-- subject [Link] -> Memora Subject
|   `-- total_lessons [Int]
|-- Memora Track [DocType, 14 fields]
|   |-- filter_section [Section Break]
|   |-- admin_filter_html [HTML]
|   |-- main_section [Section Break]
|   |-- track_title [Data]
|   |-- subject [Link] -> Memora Subject
|   |-- sort_order [Int]
|   |-- image [Attach Image]
|   |-- description [Small Text]
|   |-- is_sold_separately [Check]
|   |-- is_published [Check]
|   |-- is_linear [Check]
|   |-- sb_stats [Section Break]
|   |-- total_units [Int]
|   `-- total_lessons [Int]
|-- Memora Unit [DocType, 13 fields]
|   |-- filter_section [Section Break]
|   |-- admin_filter_html [HTML]
|   |-- main_section [Section Break]
|   |-- unit_title [Data]
|   |-- track [Link] -> Memora Track
|   |-- subject [Link] -> Memora Subject
|   |-- sort_order [Int]
|   |-- is_free [Check]
|   |-- is_published [Check]
|   |-- is_linear [Check]
|   |-- sb_stats [Section Break]
|   |-- total_topics [Int]
|   `-- total_lessons [Int]
|-- Memora Voucher Allocation [DocType, 14 fields]
|   |-- allocation_type [Select]
|   |-- batch [Link] -> Memora Voucher Batch
|   |-- customer [Link] -> Customer
|   |-- status [Select]
|   |-- column_break_1 [Column Break]
|   |-- sale_model [Select]
|   |-- quantity [Int]
|   |-- allocation_date [Date]
|   |-- section_cards [Section Break]
|   |-- allocation_cards [Table] -> Memora Voucher Allocation Card
|   |   `-- Memora Voucher Allocation Card [Child DocType, 3 fields]
|   |       |-- voucher_card [Link] -> Memora Voucher Card
|   |       |-- serial_no [Data]
|   |       `-- card_status [Data]
|   |-- section_notes [Section Break]
|   |-- notes [Small Text]
|   |-- section_return [Section Break]
|   `-- return_reason [Small Text]
|-- Memora Voucher Batch [DocType, 26 fields]
|   |-- batch_name [Data]
|   |-- batch_purpose [Select]
|   |-- grant_type [Select]
|   |-- status [Select]
|   |-- column_break_1 [Column Break]
|   |-- quantity [Int]
|   |-- pin_length [Select]
|   |-- face_value [Currency]
|   |-- target_event [Link] -> Memora Live Challenge Event
|   |-- section_eligible_plans [Section Break]
|   |-- eligible_plans [Table] -> Memora Voucher Batch Eligible Plan
|   |   `-- Memora Voucher Batch Eligible Plan [Child DocType, 1 fields]
|   |       `-- plan [Link] -> Memora Academic Plan
|   |-- section_grants [Section Break]
|   |-- batch_grants [Table] -> Memora Voucher Batch Grant
|   |   `-- Memora Voucher Batch Grant [Child DocType, 3 fields]
|   |       |-- product_grant [Link] -> Memora Product Grant
|   |       |-- commission_type [Select]
|   |       `-- commission_value [Data]
|   |-- section_generation [Section Break]
|   |-- generated_count [Int]
|   |-- allocated_count [Int]
|   |-- redeemed_count [Int]
|   |-- voided_count [Int]
|   |-- expired_count [Int]
|   |-- encrypted_file_url [Data]
|   |-- section_export_history [Section Break]
|   |-- export_log [Table] -> Memora Voucher Batch Export Log
|   |   `-- Memora Voucher Batch Export Log [Child DocType, 3 fields]
|   |       |-- exported_by [Link] -> User
|   |       |-- exported_at [Datetime]
|   |       `-- card_count [Int]
|   |-- section_void [Section Break]
|   |-- void_reason [Small Text]
|   |-- section_notes [Section Break]
|   `-- notes [Small Text]
|-- Memora Voucher Card [DocType, 19 fields]
|   |-- serial_no [Data]
|   |-- pin_hmac [Data]
|   |-- batch [Link] -> Memora Voucher Batch
|   |-- batch_purpose [Select]
|   |-- status [Select]
|   |-- column_break_1 [Column Break]
|   |-- library [Link] -> Customer
|   |-- allocation [Link] -> Memora Voucher Allocation
|   |-- return_allocation [Link] -> Memora Voucher Allocation
|   |-- sale_model [Select]
|   |-- section_redemption [Section Break]
|   |-- redeemed_by [Link] -> Memora Player Profile
|   |-- redeemed_at [Datetime]
|   |-- redeemed_grant [Link] -> Memora Product Grant
|   |-- subscription_transaction [Link] -> Memora Subscription Transaction
|   |-- section_void [Section Break]
|   |-- void_reason [Small Text]
|   |-- section_recipient [Section Break]
|   `-- recipient_note [Small Text]
`-- Memora Voucher Redemption Log [DocType, 12 fields]
    |-- player [Link] -> Memora Player Profile
    |-- pin_masked [Data]
    |-- card [Link] -> Memora Voucher Card
    |-- library [Link] -> Customer
    |-- column_break_1 [Column Break]
    |-- batch [Link] -> Memora Voucher Batch
    |-- requested_grant [Link] -> Memora Product Grant
    |-- status [Select]
    |-- section_details [Section Break]
    |-- failure_reason [Data]
    |-- ip_address [Data]
    `-- timestamp [Datetime]
```
