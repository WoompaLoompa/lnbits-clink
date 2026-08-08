async def m001_initial(db):
    await db.execute(f"""
        CREATE TABLE clink.offers (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            name TEXT,
            noffer TEXT,
            pubkey TEXT,
            privkey TEXT,
            amount_msat INTEGER,
            description TEXT,
            relay TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now}
        );
        """)

    await db.execute(f"""
        CREATE TABLE clink.plans (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            name TEXT,
            amount_msat INTEGER NOT NULL,
            frequency_number INTEGER NOT NULL,
            frequency_unit TEXT NOT NULL,
            description TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now}
        );
        """)

    await db.execute(f"""
        CREATE TABLE clink.subscriptions (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            plan_id TEXT NOT NULL,
            payer_npub TEXT,
            state TEXT NOT NULL DEFAULT 'active',
            current_period_start TIMESTAMP,
            current_period_end TIMESTAMP,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now},
            updated_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now}
        );
        """)

    await db.execute(f"""
        CREATE TABLE clink.debits (
            id TEXT PRIMARY KEY,
            wallet TEXT NOT NULL,
            ndebit TEXT,
            service_pubkey TEXT,
            amount_msat INTEGER,
            frequency_number INTEGER,
            frequency_unit TEXT,
            budget_msat INTEGER,
            rules TEXT,
            state TEXT NOT NULL DEFAULT 'pending',
            k1 TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now}
        );
        """)

    await db.execute(f"""
        CREATE TABLE clink.relays (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT {db.timestamp_now}
        );
        """)
