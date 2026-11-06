-- Migration 002: Add last_successful_persistence timestamp to connector_state
-- Distinguishes transport polling success from actual article persistence operations

ALTER TABLE connector_state ADD COLUMN last_successful_persistence TEXT;
