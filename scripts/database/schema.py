"""
Database schema initialization
"""
from scripts.database.connection import get_connection


def init_db():
    """Initialize database schema on startup."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tech_products (
            product_id   SERIAL PRIMARY KEY,
            name         VARCHAR(255) NOT NULL,
            brand        VARCHAR(255),
            category     VARCHAR(255),
            image_url    TEXT,
            created_at   TIMESTAMP DEFAULT NOW()
        );
    """)

    # Phase 3 마이그레이션: 기존 tech_products 에 image_url 1개만 추가.
    # (CREATE TABLE IF NOT EXISTS 는 기존 테이블에 컬럼을 더하지 않음)
    # ★ 이미지 컬럼 1개에만 적용 — 다른 테이블/스키마는 손대지 않는다.
    cursor.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                          WHERE table_name='tech_products' AND column_name='image_url') THEN
                ALTER TABLE tech_products ADD COLUMN image_url TEXT;
            END IF;
        END $$;
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS videos (
            video_id     VARCHAR(64) PRIMARY KEY,
            product_id   INT NOT NULL REFERENCES tech_products(product_id) ON DELETE CASCADE,
            title        VARCHAR(255) NOT NULL,
            description  TEXT,
            published_at TIMESTAMP,
            thumbnail_url TEXT,
            view_count   BIGINT,
            like_count   BIGINT,
            comment_count BIGINT,
            created_at   TIMESTAMP DEFAULT NOW()
        );
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_videos_product ON videos(product_id);
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            comment_id        VARCHAR(255) PRIMARY KEY,
            video_id          VARCHAR(64) NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
            parent_id         VARCHAR(255),
            text_raw          TEXT NOT NULL,
            is_product_related BOOLEAN,
            created_at        TIMESTAMP DEFAULT NOW(),
            -- Agent 통합을 위한 추가 메타데이터
            author_name       VARCHAR(500),
            author_channel_id VARCHAR(255),
            like_count        INTEGER DEFAULT 0,
            reply_count       INTEGER DEFAULT 0,
            published_at      TIMESTAMPTZ,
            updated_at        TIMESTAMPTZ,
            collected_at      TIMESTAMPTZ DEFAULT NOW(),
            collection_batch_id UUID,
            is_reply          BOOLEAN DEFAULT FALSE
        );
    """)
    
    # Migration: Add new columns to existing comments table
    cursor.execute("""
        DO $$ 
        BEGIN
            -- Add author_name if not exists
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                          WHERE table_name='comments' AND column_name='author_name') THEN
                ALTER TABLE comments ADD COLUMN author_name VARCHAR(500);
            END IF;
            
            -- Add author_channel_id if not exists
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                          WHERE table_name='comments' AND column_name='author_channel_id') THEN
                ALTER TABLE comments ADD COLUMN author_channel_id VARCHAR(255);
            END IF;
            
            -- Add like_count if not exists
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                          WHERE table_name='comments' AND column_name='like_count') THEN
                ALTER TABLE comments ADD COLUMN like_count INTEGER DEFAULT 0;
            END IF;
            
            -- Add reply_count if not exists
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                          WHERE table_name='comments' AND column_name='reply_count') THEN
                ALTER TABLE comments ADD COLUMN reply_count INTEGER DEFAULT 0;
            END IF;
            
            -- Add published_at if not exists
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                          WHERE table_name='comments' AND column_name='published_at') THEN
                ALTER TABLE comments ADD COLUMN published_at TIMESTAMPTZ;
            END IF;
            
            -- Add updated_at if not exists
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                          WHERE table_name='comments' AND column_name='updated_at') THEN
                ALTER TABLE comments ADD COLUMN updated_at TIMESTAMPTZ;
            END IF;
            
            -- Add collected_at if not exists
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                          WHERE table_name='comments' AND column_name='collected_at') THEN
                ALTER TABLE comments ADD COLUMN collected_at TIMESTAMPTZ DEFAULT NOW();
            END IF;
            
            -- Add collection_batch_id if not exists
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                          WHERE table_name='comments' AND column_name='collection_batch_id') THEN
                ALTER TABLE comments ADD COLUMN collection_batch_id UUID;
            END IF;
            
            -- Add is_reply if not exists
            IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                          WHERE table_name='comments' AND column_name='is_reply') THEN
                ALTER TABLE comments ADD COLUMN is_reply BOOLEAN DEFAULT FALSE;
            END IF;
            
            -- Modify comment_id to VARCHAR(255) if needed
            IF EXISTS (SELECT 1 FROM information_schema.columns 
                      WHERE table_name='comments' AND column_name='comment_id' 
                      AND character_maximum_length < 255) THEN
                ALTER TABLE comments ALTER COLUMN comment_id TYPE VARCHAR(255);
            END IF;
            
            -- Modify parent_id to VARCHAR(255) if needed
            IF EXISTS (SELECT 1 FROM information_schema.columns 
                      WHERE table_name='comments' AND column_name='parent_id' 
                      AND character_maximum_length < 255) THEN
                ALTER TABLE comments ALTER COLUMN parent_id TYPE VARCHAR(255);
            END IF;
        END $$;
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_comments_video ON comments(video_id);
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_comments_published_at ON comments(published_at DESC);
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_comments_collected_at ON comments(collected_at DESC);
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comment_sentiments (
            id               SERIAL PRIMARY KEY,
            comment_id       VARCHAR(255) NOT NULL REFERENCES comments(comment_id) ON DELETE CASCADE,
            sentiment_label  VARCHAR(16) NOT NULL,
            sentiment_score  NUMERIC(4,3),
            analysis_weight  NUMERIC(4,3) DEFAULT 1.0,
            created_at       TIMESTAMP DEFAULT NOW()
        );
    """)

    cursor.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='comment_sentiments' AND column_name='analysis_weight'
            ) THEN
                ALTER TABLE comment_sentiments
                ADD COLUMN analysis_weight NUMERIC(4,3) DEFAULT 1.0;
            END IF;
        END $$;
    """)
    
    # Migration: Modify comment_sentiments.comment_id to VARCHAR(255)
    cursor.execute("""
        DO $$ 
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns 
                      WHERE table_name='comment_sentiments' AND column_name='comment_id' 
                      AND character_maximum_length < 255) THEN
                -- Drop FK constraint first
                ALTER TABLE comment_sentiments DROP CONSTRAINT IF EXISTS comment_sentiments_comment_id_fkey;
                -- Modify column type
                ALTER TABLE comment_sentiments ALTER COLUMN comment_id TYPE VARCHAR(255);
                -- Re-add FK constraint
                ALTER TABLE comment_sentiments ADD CONSTRAINT comment_sentiments_comment_id_fkey 
                    FOREIGN KEY (comment_id) REFERENCES comments(comment_id) ON DELETE CASCADE;
            END IF;
        END $$;
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sentiments_comment ON comment_sentiments(comment_id);
    """)
    
    # Migration: Ensure one sentiment row per comment for ON CONFLICT(comment_id)
    cursor.execute("""
        DELETE FROM comment_sentiments a
        USING comment_sentiments b
        WHERE a.comment_id = b.comment_id
          AND a.id < b.id;
    """)
    
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_comment_sentiments_comment_id
        ON comment_sentiments(comment_id);
    """)
    
    # ========================================
    # Agent 통합: 중간 처리 테이블들
    # ========================================
    
    # ENUM types for Agent
    cursor.execute("""
        DO $$ BEGIN
            CREATE TYPE filter_status AS ENUM ('PASS', 'REJECT');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    cursor.execute("""
        DO $$ BEGIN
            CREATE TYPE comment_label AS ENUM (
                'PRODUCT_OPINION',
                'VIDEO_REACTION',
                'CHATTER',
                'QUESTION',
                'OFF_TOPIC'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    cursor.execute("""
        DO $$ BEGIN
            CREATE TYPE agent_action AS ENUM (
                'ANALYZE',
                'AUXILIARY_STORE',
                'EXCLUDE',
                'HOLD',
                'RECLASSIFY'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    cursor.execute("""
        DO $$ BEGIN
            CREATE TYPE exclusion_reason AS ENUM (
                'VIDEO_REACTION',
                'CHATTER',
                'OFF_TOPIC',
                'SPAM',
                'DUPLICATE',
                'PROFANITY',
                'RULE_FILTERED',
                'LOW_CONFIDENCE',
                'OTHER'
            );
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    cursor.execute("""
        DO $$ BEGIN
            CREATE TYPE sentiment_type AS ENUM ('POSITIVE', 'NEUTRAL', 'NEGATIVE');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    # 1. 규칙 필터 결과 (1차 필터)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rule_filter_results (
            id BIGSERIAL PRIMARY KEY,
            comment_id VARCHAR(255) NOT NULL REFERENCES comments(comment_id) ON DELETE CASCADE,
            filter_status filter_status NOT NULL,
            rejected_by_rule VARCHAR(100),
            reject_reason TEXT,
            filter_metadata JSONB,
            filtered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_rule_filter_comment_id ON rule_filter_results(comment_id);
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_rule_filter_status ON rule_filter_results(filter_status);
    """)
    
    cursor.execute("""
        DELETE FROM rule_filter_results a
        USING rule_filter_results b
        WHERE a.comment_id = b.comment_id
          AND a.id < b.id;
    """)
    
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_rule_filter_results_comment_id
        ON rule_filter_results(comment_id);
    """)
    
    # 2. LLM 분류 결과 (2차 분류)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS llm_classifications (
            id BIGSERIAL PRIMARY KEY,
            comment_id VARCHAR(255) NOT NULL REFERENCES comments(comment_id) ON DELETE CASCADE,
            predicted_label comment_label NOT NULL,
            confidence_score NUMERIC(5, 4) NOT NULL,
            label_scores JSONB,
            model_name VARCHAR(100),
            reasoning TEXT,
            classified_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_llm_class_comment_id ON llm_classifications(comment_id);
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_llm_class_label ON llm_classifications(predicted_label);
    """)
    
    cursor.execute("""
        DELETE FROM llm_classifications a
        USING llm_classifications b
        WHERE a.comment_id = b.comment_id
          AND a.id < b.id;
    """)
    
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_classifications_comment_id
        ON llm_classifications(comment_id);
    """)
    
    # 3. Agent 최종 결정 (3차 결정)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS agent_decisions (
            id BIGSERIAL PRIMARY KEY,
            comment_id VARCHAR(255) NOT NULL REFERENCES comments(comment_id) ON DELETE CASCADE,
            final_action agent_action NOT NULL,
            exclusion_reason exclusion_reason,
            exclusion_details TEXT,
            decision_reasoning TEXT,
            needs_human_review BOOLEAN DEFAULT FALSE,
            agent_version VARCHAR(50),
            decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_dec_comment_id ON agent_decisions(comment_id);
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_agent_dec_action ON agent_decisions(final_action);
    """)
    
    cursor.execute("""
        DELETE FROM agent_decisions a
        USING agent_decisions b
        WHERE a.comment_id = b.comment_id
          AND a.id < b.id;
    """)
    
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_decisions_comment_id
        ON agent_decisions(comment_id);
    """)
    
    # 4. Aspect 정의
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS aspect_definitions (
            id SERIAL PRIMARY KEY,
            aspect_name VARCHAR(100) NOT NULL UNIQUE,
            aspect_name_en VARCHAR(100),
            category VARCHAR(50),
            description TEXT,
            keywords TEXT[],
            is_active BOOLEAN DEFAULT TRUE,
            display_order INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    
    # 5. Aspect 추출 (항목별 감정 분석)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS aspect_extractions (
            id BIGSERIAL PRIMARY KEY,
            comment_id VARCHAR(255) NOT NULL REFERENCES comments(comment_id) ON DELETE CASCADE,
            aspect_id INTEGER REFERENCES aspect_definitions(id) ON DELETE SET NULL,
            aspect_name VARCHAR(100),
            mention_text TEXT,
            mention_context TEXT,
            aspect_sentiment sentiment_type,
            aspect_sentiment_score NUMERIC(5, 4),
            extraction_confidence NUMERIC(5, 4),
            extracted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_aspect_ext_comment_id ON aspect_extractions(comment_id);
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_aspect_ext_aspect_id ON aspect_extractions(aspect_id);
    """)
    
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_aspect_ext_sentiment ON aspect_extractions(aspect_sentiment);
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_transcripts (
            video_id        VARCHAR(64) PRIMARY KEY REFERENCES videos(video_id) ON DELETE CASCADE,
            transcript_text TEXT NOT NULL,
            language_code   VARCHAR(16),
            segment_count   INT,
            source          VARCHAR(32) DEFAULT 'youtube_transcript_api',
            updated_at      TIMESTAMP DEFAULT NOW()
        );
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_reports (
            video_id            VARCHAR(64) PRIMARY KEY REFERENCES videos(video_id) ON DELETE CASCADE,
            transcript_report   TEXT,
            comment_report      TEXT,
            integrated_report   TEXT,
            updated_at          TIMESTAMP DEFAULT NOW()
        );
    """)

    # Migration: Add integrated_report column if it doesn't exist
    cursor.execute("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'video_reports' AND column_name = 'integrated_report'
        )
    """)
    if not cursor.fetchone()[0]:
        cursor.execute("""
            ALTER TABLE video_reports
            ADD COLUMN integrated_report TEXT
        """)
        print("✓ Added integrated_report column")

    # ========================================
    # FR-005: Video Selection Agent 테이블
    # ========================================

    # videos 테이블 확장 (채널 메타, duration, selection_mode)
    cursor.execute("""
        ALTER TABLE videos ADD COLUMN IF NOT EXISTS channel_id VARCHAR(64);
    """)
    cursor.execute("""
        ALTER TABLE videos ADD COLUMN IF NOT EXISTS channel_name VARCHAR(255);
    """)
    cursor.execute("""
        ALTER TABLE videos ADD COLUMN IF NOT EXISTS channel_subscriber_count BIGINT;
    """)
    cursor.execute("""
        ALTER TABLE videos ADD COLUMN IF NOT EXISTS duration_seconds INTEGER;
    """)
    cursor.execute("""
        ALTER TABLE videos ADD COLUMN IF NOT EXISTS selection_mode VARCHAR(16);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);
    """)

    # video_selection_runs: 실행 1회 = 1row (audit trail)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_selection_runs (
            run_id          UUID PRIMARY KEY,
            product_id      INT NOT NULL REFERENCES tech_products(product_id) ON DELETE CASCADE,
            mode            VARCHAR(16) NOT NULL,
            model_used      VARCHAR(64),
            policy_version  VARCHAR(32),
            k_selected      INTEGER NOT NULL,
            candidate_count INTEGER NOT NULL,
            trace_json      JSONB,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_selection_runs_product ON video_selection_runs(product_id);
    """)

    # video_selection_scores: 후보 × run (선정/미선정 + 점수 + rationale)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS video_selection_scores (
            id               BIGSERIAL PRIMARY KEY,
            run_id           UUID NOT NULL REFERENCES video_selection_runs(run_id) ON DELETE CASCADE,
            video_id         VARCHAR(64) NOT NULL,
            selected         BOOLEAN NOT NULL,
            rank             INTEGER,
            final_score      NUMERIC(6,4),
            dimensions_json  JSONB NOT NULL,
            tier             VARCHAR(16),
            rationale_short  TEXT,
            rationale_full   TEXT,
            created_at       TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_selection_scores_run ON video_selection_scores(run_id);
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_selection_scores_video ON video_selection_scores(video_id);
    """)

    # ========================================
    # 제품 단위 통합 인사이트 보고서 (입력: 영상별 자막 보고서 N건)
    # ========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_integrated_reports (
            id                   SERIAL PRIMARY KEY,
            product_id           INT NOT NULL REFERENCES tech_products(product_id) ON DELETE CASCADE,
            video_ids            TEXT NOT NULL,
            source_video_count   INT NOT NULL,
            report_text          TEXT NOT NULL,
            model_used           VARCHAR(64),
            created_at           TIMESTAMP DEFAULT NOW()
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_pir_product ON product_integrated_reports(product_id);
    """)

    # ========================================
    # 사용량 추적 (채널별 UTM + funnel)
    # ========================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usage_events (
            id            BIGSERIAL PRIMARY KEY,
            session_uuid  UUID NOT NULL,
            utm_source    VARCHAR(32),
            event_type    VARCHAR(32) NOT NULL,
            path          VARCHAR(512),
            product_id    INT,
            video_id      VARCHAR(64),
            referrer      TEXT,
            user_agent    VARCHAR(512),
            extra         JSONB,
            ts            TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_session ON usage_events(session_uuid);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_utm ON usage_events(utm_source);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_event_type ON usage_events(event_type);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts);")

    # ── Phase 5: 팝업용 제품 메타(가격·스펙) 캐시 ──────────────────
    # ★ 신규 테이블 1개만. 기존 14테이블·tech_products 무변경(§15 합의).
    # 외부 출처(공식 제조사 검색)에서 한 번 가져온 값을 재사용해 외부 호출
    # 폭증·지연을 막는다. 실패해도 팝업 자체는 §7 fallback 으로 계속 동작.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_meta_cache (
            product_id    INT PRIMARY KEY REFERENCES tech_products(product_id) ON DELETE CASCADE,
            price_raw     INTEGER,       -- 원 단위 정수(예: 1290000) — 표시는 만원 단위
            price_display VARCHAR(64),   -- 사람이 보는 표기(예: "최저 129만 원~")
            screen_size   VARCHAR(32),   -- 예: "6.7인치"
            release_year  INTEGER,       -- 예: 2026
            source        TEXT,          -- 출처 메타(URL 등, 디버그용)
            fetched_at    TIMESTAMP DEFAULT NOW()
        );
    """)

    # ========================================
    # 검색 후보 자동 제안 (search-suggest)
    # ========================================
    # tech_products.seeded — seed_products.py 가 일괄 적재한 row 표시. 롤백/검수 용이.
    cursor.execute("""
        ALTER TABLE tech_products
        ADD COLUMN IF NOT EXISTS seeded BOOLEAN DEFAULT FALSE;
    """)

    # (LOWER(name), LOWER(brand)) UNIQUE INDEX — 정규화 중복 방지.
    # 기존 row 충돌 시 init_db 가 부팅 자체를 막을 수 있으므로 dry-run 가드.
    cursor.execute("""
        SELECT LOWER(name) AS lname, COALESCE(LOWER(brand), '') AS lbrand, COUNT(*) AS cnt
        FROM tech_products
        GROUP BY 1, 2
        HAVING COUNT(*) > 1
        LIMIT 5
    """)
    conflicts = cursor.fetchall()
    if conflicts:
        print(
            f"[SCHEMA][WARN] tech_products 정규화 중복 {len(conflicts)}건 — "
            f"UNIQUE INDEX uq_tech_products_name_brand_ci 생성 SKIP "
            f"(예: {conflicts[0]}). 운영 정리 후 재기동하면 활성화됨."
        )
    else:
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_tech_products_name_brand_ci
              ON tech_products (LOWER(name), COALESCE(LOWER(brand), ''));
        """)

    # suggest_cache — Container Apps replica 간 공유, TTL 기반 만료.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS suggest_cache (
            query_norm    VARCHAR(255) PRIMARY KEY,
            response_json JSONB NOT NULL,
            created_at    TIMESTAMPTZ DEFAULT NOW(),
            expires_at    TIMESTAMPTZ NOT NULL
        );
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_suggest_cache_expires
          ON suggest_cache(expires_at);
    """)

    conn.commit()
    cursor.close()
    conn.close()
    print("✓ Database initialized")
