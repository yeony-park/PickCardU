CREATE TABLE `evaluation_cases` (
	`id` text PRIMARY KEY NOT NULL,
	`run_id` text NOT NULL,
	`payload` text NOT NULL,
	FOREIGN KEY (`run_id`) REFERENCES `evaluations`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE INDEX `evaluation_cases_run_id_idx` ON `evaluation_cases` (`run_id`);--> statement-breakpoint
CREATE TABLE `evaluations` (
	`id` text PRIMARY KEY NOT NULL,
	`experiment_id` text NOT NULL,
	`created_by` text NOT NULL,
	`payload` text NOT NULL,
	FOREIGN KEY (`experiment_id`) REFERENCES `experiments`(`id`) ON UPDATE no action ON DELETE no action
);
--> statement-breakpoint
CREATE INDEX `evaluations_experiment_id_idx` ON `evaluations` (`experiment_id`);--> statement-breakpoint
CREATE TABLE `experiments` (
	`id` text PRIMARY KEY NOT NULL,
	`created_at` text NOT NULL,
	`created_by` text NOT NULL,
	`payload` text NOT NULL
);
--> statement-breakpoint
CREATE INDEX `experiments_created_at_idx` ON `experiments` (`created_at`);--> statement-breakpoint
CREATE TABLE `search_runs` (
	`id` text PRIMARY KEY NOT NULL,
	`created_at` text NOT NULL,
	`created_by` text NOT NULL,
	`payload` text NOT NULL
);
