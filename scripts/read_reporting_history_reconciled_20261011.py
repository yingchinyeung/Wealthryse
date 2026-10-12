"""Retrieve the hash-bound, metadata-only reconciled historical inventory."""
import read_reporting_history_inventory_20261011 as c
c.OUT=c.OUT.parent/'historical-reconciled-read-v1'
c.TRIAL=c.OUT.parent/'historical-reconciled-v1/result.json'
c.BLOB='candidate-stage34-historical-reconciled-v1/inventory.json'
if __name__=='__main__':c.main()
