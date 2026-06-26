"""position_hour is a TimescaleDB hypertable after the migration.

The rest of the projector suite proves the read side still *works* over
chunks (ON CONFLICT accumulation, rebuild-from-zero, the killer query,
verify's replay equality); this leg proves the conversion actually
happened - that the migration did more than leave a plain table intact.

Same gating and provisioning contract as the other integration legs; the
app schema is migrated by Alembic in the fixture, so revision 0004 is
part of what this proves.
"""

import pytest
import sqlalchemy as sa

from tests.support import needs_live_stack, provision

pytestmark = needs_live_stack


@pytest.fixture(scope="module")
def engine() -> sa.Engine:
    return provision()


def test_position_hour_is_a_hypertable(engine: sa.Engine) -> None:
    with engine.connect() as connection:
        name = connection.execute(
            sa.text(
                "SELECT hypertable_name FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'position_hour'"
            )
        ).scalar_one_or_none()
        # Partitioned on the delivery hour - the column the period-windowed
        # /positions read prunes by.
        dimension = connection.execute(
            sa.text(
                "SELECT column_name FROM timescaledb_information.dimensions "
                "WHERE hypertable_name = 'position_hour'"
            )
        ).scalar_one_or_none()
    assert name == "position_hour"
    assert dimension == "period_start"
