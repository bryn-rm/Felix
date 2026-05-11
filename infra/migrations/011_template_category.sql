-- Migration 011 — Template category
-- Adds a closed-set category column to smart_templates so the templates UI
-- can render Reply / Outreach / Follow-up / Other badges. NULL is allowed
-- for pre-existing rows so they render as "uncategorised". Run after
-- 002_phase7_smart_templates.sql.

ALTER TABLE smart_templates
ADD COLUMN category TEXT
CHECK (category IN ('reply', 'outreach', 'follow_up', 'other'));
