# z_impute.R — Imputation (MICE / missForest)
# Run directly: source("z_impute.R")

INPUT           <- "braindata.xlsx"
SHEET           <- "rad"
OUTPUT          <- "braindata_imputed.xlsx"
GROUP           <- "Group"
IMPUTE_METHOD   <- "missForest" # "mice" or "missForest"
# mice options
MAX_ITER        <- 20L
SEED            <- 66L
MICE_METHOD     <- "rf"         # rf/pmm/norm/midastouch/logreg/polyreg/lda
# missForest options
MISSFOREST_NTREE <- 100L

if (IMPUTE_METHOD == "mice" && !requireNamespace("mice", quietly = TRUE))
  stop("Install mice: install.packages('mice')")
if (IMPUTE_METHOD == "missForest" && !requireNamespace("missForest", quietly = TRUE))
  stop("Install missForest: install.packages('missForest')")

cat(sprintf("=== %s Imputation ===\n", IMPUTE_METHOD))
df <- openxlsx::read.xlsx(INPUT, sheet = SHEET)
cat(sprintf("Data: %d rows x %d cols\n", nrow(df), ncol(df)))

num_cols <- names(df)[sapply(df, is.numeric) & names(df) != GROUP]
if (length(num_cols) == 0) {
  cat("No numeric columns, nothing to do.\n")
  quit(save = "no")
}

groups <- sort(unique(df[[GROUP]]))
cat(sprintf("Numeric columns: %d, Groups: %d\n", length(num_cols), length(groups)))

for (gv in groups) {
  g_mask <- df[[GROUP]] == gv
  g_data <- df[g_mask, num_cols, drop = FALSE]
  n_miss <- sum(is.na(g_data))
  if (n_miss == 0) next

  valid <- sapply(g_data, function(x) {
    non_na <- x[!is.na(x)]
    length(non_na) >= 2 && length(unique(non_na)) > 1
  })
  if (!any(valid)) {
    cat(sprintf("  Group=%s: %d missing, insufficient variance\n", gv, n_miss))
    next
  }

  cat(sprintf("  Group=%s (n=%d): imputing %d values\n", gv, sum(g_mask), n_miss))

  if (IMPUTE_METHOD == "mice") {
    imp <- tryCatch(
      mice::mice(g_data[, valid, drop = FALSE], m = 1, maxit = MAX_ITER,
                 seed = SEED, method = MICE_METHOD, printFlag = FALSE),
      error = function(e) { cat(sprintf("    MICE error: %s\n", e$message)); NULL }
    )
    if (!is.null(imp)) {
      df[g_mask, names(g_data[, valid, drop = FALSE])] <- mice::complete(imp, 1)
    }
  } else {
    imp <- tryCatch(
      missForest::missForest(g_data[, valid, drop = FALSE],
                              ntree = MISSFOREST_NTREE, maxiter = MAX_ITER,
                              variablewise = FALSE, verbose = FALSE),
      error = function(e) { cat(sprintf("    missForest error: %s\n", e$message)); NULL }
    )
    if (!is.null(imp)) {
      df[g_mask, names(g_data[, valid, drop = FALSE])] <- imp$ximp
    }
  }
}

openxlsx::write.xlsx(df, OUTPUT, overwrite = TRUE)
cat(sprintf("Done → %s\n", OUTPUT))