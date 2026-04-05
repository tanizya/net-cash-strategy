-- jpx_stocks: Net cash positive Japanese stocks (daily overwrite)
-- Run this in Supabase SQL Editor: https://supabase.com/dashboard/project/vlyauihvpnqohekufifj/sql

CREATE TABLE IF NOT EXISTS public.jpx_stocks (
  code           TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  market         TEXT,                    -- プライム / スタンダード
  sector_jp      TEXT,                    -- 33業種区分
  sector         TEXT,                    -- English sector tag
  price          NUMERIC,                -- 直近終値
  unit_cost      INTEGER,                -- 100株購入価格
  market_cap     BIGINT,                 -- 時価総額 (円)
  net_cash       BIGINT,                 -- ネットキャッシュ (円, 正=キャッシュ超)
  net_cash_ratio NUMERIC,                -- NC / 時価総額
  pbr            NUMERIC,
  op_margin      NUMERIC,                -- 営業利益率 (0.0-1.0)
  beta           NUMERIC,
  month_high     NUMERIC,                -- 1ヶ月高値
  month_low      NUMERIC,                -- 1ヶ月安値
  month_change   NUMERIC,                -- 1ヶ月変動率 (%)
  rs             INTEGER,                -- Relative Strength 1-99
  rsi            NUMERIC,                -- RSI(14)
  score          NUMERIC,                -- 選定スコア
  strategy       TEXT,                   -- NULL | 'net_cash_select' | future strategies
  signal         TEXT DEFAULT 'WAIT',    -- WAIT / BUY / EXIT
  tags           JSONB,                  -- タグ配列
  updated_at     TIMESTAMPTZ DEFAULT now()
);

-- Index for strategy filtering
CREATE INDEX IF NOT EXISTS idx_jpx_stocks_strategy ON public.jpx_stocks (strategy) WHERE strategy IS NOT NULL;

-- Index for RS ranking
CREATE INDEX IF NOT EXISTS idx_jpx_stocks_rs ON public.jpx_stocks (rs DESC);

-- RLS: allow public read
ALTER TABLE public.jpx_stocks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow public read" ON public.jpx_stocks FOR SELECT USING (true);
