"""Booked-agent identification and propensity modeling.

A self-contained analysis subsystem that ingests Instagram DMs, iMessages, and
Square / Cash App payment records, resolves them into unified agent identities,
labels who actually booked (payments = ground truth), and trains a propensity
model for which agent types are most likely to book.

Pipeline order:
    payments_ingest -> dm_ingest / imsg_ingest -> identity_match -> label_booked
    -> features -> train_booked  (eda notebook reads the same intermediates)
"""
