CREATE TABLE ai_calls (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES auth.users(id),
    feature TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INT,
    output_tokens INT,
    latency_ms INT,
    success BOOLEAN NOT NULL DEFAULT true,
    parse_error BOOLEAN NOT NULL DEFAULT false,
    error_message TEXT,
    -- Quota accounting. ai_calls stays the observability log, but user limits
    -- are metered on cost-weighted units rather than raw row count so that
    -- background retries (triage/commitment) can't starve a user's interactive
    -- budget. quota_scope partitions the spend; billable_units is NULL when the
    -- provider returned no usage (e.g. a failed "credit balance" call), so
    -- failed calls never consume quota.
    quota_scope TEXT NOT NULL DEFAULT 'interactive'
        CHECK (quota_scope IN ('interactive', 'background', 'system')),
    billable_tokens INT,
    billable_units NUMERIC,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_ai_calls_feature_created
    ON ai_calls(feature, created_at DESC);
CREATE INDEX idx_ai_calls_user_created
    ON ai_calls(user_id, created_at DESC);
CREATE INDEX idx_ai_calls_success
    ON ai_calls(success, feature);
-- Supports the monthly per-user interactive quota SUM(billable_units) query.
CREATE INDEX idx_ai_calls_user_quota_month
    ON ai_calls(user_id, created_at DESC, quota_scope);
CREATE INDEX idx_ai_calls_user_billable_month
    ON ai_calls(user_id, created_at DESC)
    WHERE billable_units IS NOT NULL;
ALTER TABLE ai_calls ENABLE ROW LEVEL SECURITY;
CREATE POLICY "service role only on ai_calls"
    ON ai_calls FOR ALL
    USING (auth.role() = 'service_role');

CREATE TABLE ai_feedback (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id),
    ai_call_id UUID REFERENCES ai_calls(id),
    feature TEXT NOT NULL,
    rating SMALLINT NOT NULL CHECK (rating IN (1,2,3)),
    correction TEXT,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE ai_feedback ENABLE ROW LEVEL SECURITY;
CREATE POLICY "users manage own feedback"
    ON ai_feedback FOR ALL
    USING (user_id = auth.uid());

CREATE TABLE eval_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    feature TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    total_fixtures INT NOT NULL,
    passed INT NOT NULL,
    failed INT NOT NULL,
    pass_rate FLOAT NOT NULL,
    failures JSONB,
    run_at TIMESTAMPTZ DEFAULT NOW()
);
