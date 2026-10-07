import { sqliteTable, text, index } from 'drizzle-orm/sqlite-core';

export const experiments = sqliteTable('experiments', {
  id: text('id').primaryKey(), createdAt: text('created_at').notNull(),
  createdBy: text('created_by').notNull(), payload: text('payload').notNull(),
}, table => [index('experiments_created_at_idx').on(table.createdAt)]);

export const evaluations = sqliteTable('evaluations', {
  id: text('id').primaryKey(), experimentId: text('experiment_id').notNull().references(() => experiments.id),
  createdBy: text('created_by').notNull(), payload: text('payload').notNull(),
}, table => [index('evaluations_experiment_id_idx').on(table.experimentId)]);

export const evaluationCases = sqliteTable('evaluation_cases', {
  id: text('id').primaryKey(), runId: text('run_id').notNull().references(() => evaluations.id),
  payload: text('payload').notNull(),
}, table => [index('evaluation_cases_run_id_idx').on(table.runId)]);

export const searchRuns = sqliteTable('search_runs', {
  id: text('id').primaryKey(), createdAt: text('created_at').notNull(),
  createdBy: text('created_by').notNull(), payload: text('payload').notNull(),
});
