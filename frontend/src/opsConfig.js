// Form definitions for every transformation the backend supports (backend/ops.py).
//
// Field kinds: column, columns, text, number, select, checkbox, textarea,
// conditions, aggs, sortby, path. `show(params)` hides a field conditionally;
// `toParams(params)` reshapes the form state into what the API expects.

const AGGS = [['sum', 'Sum'], ['mean', 'Mean'], ['median', 'Median'], ['min', 'Min'], ['max', 'Max'],
  ['count', 'Count'], ['n_unique', 'Distinct count'], ['first', 'First'], ['last', 'Last'], ['std', 'Std dev'], ['var', 'Variance']];

export const FILTER_OPERATORS = [
  ['eq', '='], ['ne', '≠'], ['gt', '>'], ['ge', '≥'], ['lt', '<'], ['le', '≤'],
  ['between', 'between'], ['in', 'is one of'], ['not_in', 'is not one of'],
  ['contains', 'contains'], ['not_contains', "doesn't contain"], ['starts_with', 'starts with'],
  ['ends_with', 'ends with'], ['regex', 'matches regex'], ['is_null', 'is empty'], ['is_not_null', 'is not empty'],
];
export const NO_VALUE_OPERATORS = ['is_null', 'is_not_null'];

const commaList = v => (Array.isArray(v) ? v : String(v || '').split(',').map(s => s.trim()).filter(Boolean));

export const OP_GROUPS = [
  {
    name: 'Columns',
    ops: [
      { type: 'select_columns', label: 'Keep columns', fields: [{ key: 'columns', label: 'Columns to keep (in this order)', kind: 'columns' }] },
      { type: 'drop_column', label: 'Drop columns', fields: [{ key: 'columns', label: 'Columns', kind: 'columns' }] },
      {
        type: 'rename_column', label: 'Rename column',
        fields: [{ key: 'oldCol', label: 'Column', kind: 'column' }, { key: 'newName', label: 'New name', kind: 'text' }],
        toParams: p => ({ mapping: { [p.oldCol]: p.newName } }),
        fromParams: p => { const [oldCol, newName] = Object.entries(p.mapping || {})[0] || []; return { oldCol, newName }; },
      },
      {
        type: 'cast_column', label: 'Change type',
        defaults: { dtype: 'Float64' },
        fields: [
          { key: 'columns', label: 'Columns', kind: 'columns' },
          { key: 'dtype', label: 'New type', kind: 'select', options: [['Int64', 'Integer'], ['Float64', 'Decimal'], ['String', 'Text'], ['Boolean', 'True/False'], ['Date', 'Date'], ['Datetime', 'Date & time'], ['Categorical', 'Category']] },
          { key: 'format', label: 'Date format (optional, e.g. %d/%m/%Y)', kind: 'text', show: p => ['Date', 'Datetime'].includes(p.dtype) },
          { key: 'clean', label: 'Strip symbols first (1,000 / $5 / 12%)', kind: 'checkbox', show: p => ['Int64', 'Float64'].includes(p.dtype) },
        ],
      },
      {
        type: 'add_column', label: 'Add calculated column',
        fields: [
          { key: 'name', label: 'New column name', kind: 'text' },
          { key: 'expression', label: 'Formula (SQL)', kind: 'textarea', placeholder: 'price * qty\nCASE WHEN age >= 18 THEN \'adult\' ELSE \'minor\' END\nUPPER(name)' },
        ],
        hint: 'Use column names directly. Quote names with spaces: "order id".',
      },
    ],
  },
  {
    name: 'Rows',
    ops: [
      {
        type: 'filter_rows', label: 'Filter rows',
        defaults: { conditions: [{ column: '', operator: 'eq', value: '' }], combine: 'and' },
        fields: [{ key: 'conditions', label: 'Keep rows where', kind: 'conditions' }],
      },
      {
        type: 'sort', label: 'Sort',
        defaults: { by: [{ column: '', descending: false }] },
        fields: [{ key: 'by', label: 'Sort by', kind: 'sortby' }],
        toParams: p => ({ by: (p.by || []).filter(b => b.column) }),
      },
      {
        type: 'limit_rows', label: 'Keep top / first N',
        defaults: { n: 10, descending: true },
        fields: [
          { key: 'n', label: 'Number of rows', kind: 'number' },
          { key: 'by', label: 'Ranked by (optional)', kind: 'column', optional: true },
          { key: 'descending', label: 'Largest first', kind: 'checkbox', show: p => !!p.by },
        ],
      },
      {
        type: 'sample_rows', label: 'Random sample',
        defaults: { mode: 'n', n: 1000, seed: 42 },
        fields: [
          { key: 'mode', label: 'Sample by', kind: 'select', options: [['n', 'Number of rows'], ['fraction', 'Percentage']] },
          { key: 'n', label: 'Rows', kind: 'number', show: p => p.mode !== 'fraction' },
          { key: 'percent', label: 'Percent of rows', kind: 'number', show: p => p.mode === 'fraction' },
          { key: 'seed', label: 'Seed (same seed = same sample)', kind: 'number' },
        ],
        toParams: p => (p.mode === 'fraction' ? { fraction: Number(p.percent) / 100, seed: p.seed } : { n: p.n, seed: p.seed }),
        fromParams: p => (p.fraction ? { mode: 'fraction', percent: p.fraction * 100, seed: p.seed } : { mode: 'n', n: p.n, seed: p.seed }),
      },
      {
        type: 'drop_duplicates', label: 'Remove duplicates',
        defaults: { keep: 'first' },
        fields: [
          { key: 'columns', label: 'Compare on (empty = all columns)', kind: 'columns', optional: true },
          { key: 'keep', label: 'Keep', kind: 'select', options: [['first', 'First occurrence'], ['last', 'Last occurrence'], ['none', 'No duplicated rows at all']] },
        ],
      },
    ],
  },
  {
    name: 'Text',
    ops: [
      {
        type: 'text_transform', label: 'Clean up text',
        defaults: { transform: 'strip' },
        fields: [
          { key: 'columns', label: 'Columns', kind: 'columns', types: 'text' },
          { key: 'transform', label: 'Change', kind: 'select', options: [['strip', 'Trim spaces'], ['collapse_spaces', 'Collapse repeated spaces'], ['lower', 'lowercase'], ['upper', 'UPPERCASE'], ['title', 'Title Case'], ['strip_start', 'Trim start'], ['strip_end', 'Trim end'], ['length', 'Length (characters)']] },
          { key: 'new_column', label: 'Write to new column (optional)', kind: 'text', show: p => (p.columns || []).length <= 1 },
        ],
      },
      {
        type: 'split_column', label: 'Split column',
        defaults: { delimiter: ',', parts: 2 },
        fields: [
          { key: 'column', label: 'Column', kind: 'column' },
          { key: 'delimiter', label: 'Split on', kind: 'text' },
          { key: 'parts', label: 'Number of parts', kind: 'number' },
          { key: 'names', label: 'New names (optional, comma separated)', kind: 'text' },
          { key: 'drop_original', label: 'Remove original column', kind: 'checkbox' },
        ],
      },
      {
        type: 'regex_extract', label: 'Extract with pattern',
        defaults: { group: 1 },
        fields: [
          { key: 'column', label: 'Column', kind: 'column' },
          { key: 'pattern', label: 'Regex with a (group)', kind: 'text', placeholder: '(\\d{4})' },
          { key: 'group', label: 'Group number', kind: 'number' },
          { key: 'new_column', label: 'New column name', kind: 'text' },
        ],
      },
      {
        type: 'find_replace', label: 'Find & replace',
        defaults: { mode: 'contains', replace: '' },
        fields: [
          { key: 'columns', label: 'Columns', kind: 'columns' },
          { key: 'mode', label: 'Match', kind: 'select', options: [['contains', 'Text anywhere in the cell'], ['whole_cell', 'Whole cell value'], ['regex', 'Regular expression']] },
          { key: 'find', label: 'Find', kind: 'text' },
          { key: 'replace', label: 'Replace with', kind: 'text' },
        ],
      },
      {
        type: 'parse_dates', label: 'Parse dates',
        defaults: { to: 'date' },
        fields: [
          { key: 'column', label: 'Text column', kind: 'column', types: 'text' },
          { key: 'to', label: 'As', kind: 'select', options: [['date', 'Date'], ['datetime', 'Date & time']] },
          { key: 'format', label: 'Format (optional, e.g. %d/%m/%Y)', kind: 'text' },
          { key: 'new_column', label: 'Write to new column (optional)', kind: 'text' },
        ],
      },
    ],
  },
  {
    name: 'Missing values',
    ops: [
      {
        type: 'fill_missing', label: 'Fill empty cells',
        defaults: { strategy: 'value' },
        fields: [
          { key: 'columns', label: 'Columns', kind: 'columns' },
          { key: 'strategy', label: 'Fill with', kind: 'select', options: [['value', 'A fixed value'], ['forward', 'Previous value'], ['backward', 'Next value'], ['mean', 'Column mean'], ['median', 'Column median'], ['min', 'Column min'], ['max', 'Column max'], ['zero', 'Zero']] },
          { key: 'value', label: 'Value', kind: 'text', show: p => p.strategy === 'value' },
          { key: 'group_by', label: 'Separately for each (optional)', kind: 'columns', optional: true, show: p => !['value', 'zero'].includes(p.strategy) },
        ],
      },
      {
        type: 'drop_missing', label: 'Drop rows with empty cells',
        defaults: { how: 'any' },
        fields: [
          { key: 'columns', label: 'Check columns (empty = all)', kind: 'columns', optional: true },
          { key: 'how', label: 'Drop a row if', kind: 'select', options: [['any', 'Any checked cell is empty'], ['all', 'All checked cells are empty']] },
        ],
      },
    ],
  },
  {
    name: 'Calculate',
    ops: [
      {
        type: 'bin_column', label: 'Group numbers into ranges',
        defaults: { method: 'breaks' },
        fields: [
          { key: 'column', label: 'Numeric column', kind: 'column', types: 'numeric' },
          { key: 'method', label: 'Ranges', kind: 'select', options: [['breaks', 'At values I choose'], ['quantiles', 'Equal-sized groups (quantiles)']] },
          { key: 'breaks', label: 'Break points (comma separated)', kind: 'text', placeholder: '18, 35, 65', show: p => p.method !== 'quantiles' },
          { key: 'bins', label: 'Number of groups', kind: 'number', show: p => p.method === 'quantiles' },
          { key: 'labels', label: 'Labels (optional, comma separated)', kind: 'text' },
          { key: 'new_column', label: 'New column name', kind: 'text' },
        ],
      },
      {
        type: 'window', label: 'Rank / running total / lag',
        defaults: { function: 'cum_sum', offset: 1 },
        fields: [
          { key: 'function', label: 'Calculation', kind: 'select', options: [['cum_sum', 'Running total'], ['cum_max', 'Running max'], ['cum_min', 'Running min'], ['cum_count', 'Running count'], ['rank', 'Rank'], ['dense_rank', 'Dense rank'], ['row_number', 'Row number'], ['lag', 'Previous value (lag)'], ['lead', 'Next value (lead)'], ['diff', 'Difference from previous'], ['pct_change', '% change from previous']] },
          { key: 'column', label: 'Column', kind: 'column', show: p => p.function !== 'row_number' },
          { key: 'partition_by', label: 'Restart for each (optional)', kind: 'columns', optional: true },
          { key: 'order_by', label: 'In order of (optional)', kind: 'column', optional: true, show: p => !['rank', 'dense_rank'].includes(p.function) },
          { key: 'descending', label: 'Descending', kind: 'checkbox' },
          { key: 'offset', label: 'Rows back/ahead', kind: 'number', show: p => ['lag', 'lead', 'diff', 'pct_change'].includes(p.function) },
          { key: 'new_column', label: 'New column name (optional)', kind: 'text' },
        ],
      },
    ],
  },
  {
    name: 'Machine learning prep',
    ops: [
      {
        type: 'train_test_split', label: 'Train / validation / test split',
        defaults: { test_size: 20, val_size: 0, seed: 42, column: 'split' },
        fields: [
          { key: 'test_size', label: 'Test %', kind: 'number' },
          { key: 'val_size', label: 'Validation % (optional)', kind: 'number' },
          { key: 'stratify', label: 'Keep class balance of (optional)', kind: 'column', optional: true },
          { key: 'group', label: 'Keep groups together, e.g. a customer id (optional)', kind: 'column', optional: true, show: p => !p.stratify },
          { key: 'seed', label: 'Seed (same seed = same split)', kind: 'number' },
          { key: 'column', label: 'Name of the new split column', kind: 'text' },
        ],
        hint: 'Adds a column with train / val / test. Sizes are exact.',
        toParams: p => ({ ...p, test_size: Number(p.test_size) / 100, val_size: Number(p.val_size || 0) / 100 }),
        fromParams: p => ({ ...p, test_size: Math.round(p.test_size * 1000) / 10, val_size: Math.round((p.val_size || 0) * 1000) / 10 }),
      },
      {
        type: 'one_hot_encode', label: 'One-hot encode',
        defaults: { max_categories: 20, drop_original: true },
        fields: [
          { key: 'columns', label: 'Category columns', kind: 'columns' },
          { key: 'max_categories', label: 'Most frequent categories to keep (rest → _other)', kind: 'number' },
          { key: 'drop_original', label: 'Remove the original columns', kind: 'checkbox' },
          { key: 'fit_on', label: 'Learn categories only from rows where (optional)', kind: 'fit_on' },
        ],
        hint: 'Categories are learned now and reused when this step replays or runs as a recipe.',
      },
      {
        type: 'label_encode', label: 'Label encode (category → number)',
        defaults: { order: 'alphabetical', suffix: '_code' },
        fields: [
          { key: 'columns', label: 'Category columns', kind: 'columns' },
          { key: 'order', label: 'Number them', kind: 'select', options: [['alphabetical', 'Alphabetically'], ['frequency', 'Most common first']] },
          { key: 'suffix', label: 'New column suffix (empty = replace)', kind: 'text' },
          { key: 'fit_on', label: 'Learn categories only from rows where (optional)', kind: 'fit_on' },
        ],
        hint: 'Unseen values become -1.',
      },
      {
        type: 'scale', label: 'Scale numbers',
        defaults: { method: 'standard' },
        fields: [
          { key: 'columns', label: 'Numeric columns', kind: 'columns', types: 'numeric' },
          { key: 'method', label: 'Method', kind: 'select', options: [['standard', 'Standardize (mean 0, std 1)'], ['minmax', 'Min-max (0 to 1)'], ['robust', 'Robust (median & IQR)']] },
          { key: 'suffix', label: 'New column suffix (empty = replace)', kind: 'text' },
          { key: 'fit_on', label: 'Learn statistics only from rows where (optional)', kind: 'fit_on' },
        ],
      },
      {
        type: 'clip_outliers', label: 'Clip outliers',
        defaults: { method: 'iqr', k: 1.5, lower_pct: 1, upper_pct: 99 },
        fields: [
          { key: 'columns', label: 'Numeric columns', kind: 'columns', types: 'numeric' },
          { key: 'method', label: 'Limits from', kind: 'select', options: [['iqr', 'IQR rule (Q1 − k·IQR, Q3 + k·IQR)'], ['percentile', 'Percentiles'], ['values', 'Fixed values']] },
          { key: 'k', label: 'k', kind: 'number', show: p => p.method === 'iqr' },
          { key: 'lower_pct', label: 'Lower percentile', kind: 'number', show: p => p.method === 'percentile' },
          { key: 'upper_pct', label: 'Upper percentile', kind: 'number', show: p => p.method === 'percentile' },
          { key: 'lower', label: 'Lower limit', kind: 'number', show: p => p.method === 'values' },
          { key: 'upper', label: 'Upper limit', kind: 'number', show: p => p.method === 'values' },
          { key: 'fit_on', label: 'Learn limits only from rows where (optional)', kind: 'fit_on', show: p => p.method !== 'values' },
        ],
      },
      {
        type: 'date_parts', label: 'Date features',
        defaults: { parts: ['year', 'month', 'day', 'weekday'] },
        fields: [
          { key: 'column', label: 'Date column', kind: 'column', types: 'date' },
          { key: 'parts', label: 'Features', kind: 'multiselect', options: [['year', 'Year'], ['quarter', 'Quarter'], ['month', 'Month'], ['week', 'Week of year'], ['day', 'Day of month'], ['day_of_year', 'Day of year'], ['weekday', 'Weekday (1 = Mon)'], ['is_weekend', 'Is weekend'], ['hour', 'Hour'], ['minute', 'Minute']] },
          { key: 'prefix', label: 'Prefix for new columns (default: column name + _)', kind: 'text' },
        ],
      },
    ],
  },
  {
    name: 'Reshape',
    ops: [
      {
        type: 'group_by', label: 'Group & summarize',
        defaults: { aggs: [{ column: '', agg: 'count' }] },
        fields: [
          { key: 'by', label: 'Group by', kind: 'columns' },
          { key: 'aggs', label: 'Summaries', kind: 'aggs' },
        ],
      },
      {
        type: 'pivot', label: 'Pivot (rows → columns)',
        defaults: { agg: 'first' },
        fields: [
          { key: 'index', label: 'Row labels', kind: 'columns' },
          { key: 'on', label: 'New columns from', kind: 'column' },
          { key: 'values', label: 'Values', kind: 'column' },
          { key: 'agg', label: 'Combine duplicates with', kind: 'select', options: [['first', 'First'], ['sum', 'Sum'], ['mean', 'Mean'], ['median', 'Median'], ['min', 'Min'], ['max', 'Max'], ['count', 'Count'], ['last', 'Last']] },
        ],
      },
      {
        type: 'unpivot', label: 'Unpivot (columns → rows)',
        fields: [
          { key: 'index', label: 'Columns to keep as-is', kind: 'columns' },
          { key: 'on', label: 'Columns to turn into rows (empty = all others)', kind: 'columns', optional: true },
          { key: 'variable_name', label: 'Name for the column-name column', kind: 'text', placeholder: 'variable' },
          { key: 'value_name', label: 'Name for the value column', kind: 'text', placeholder: 'value' },
        ],
      },
    ],
  },
  {
    name: 'Combine',
    ops: [
      {
        type: 'join', label: 'Join another file',
        defaults: { how: 'left' },
        fields: [
          { key: 'path', label: 'File path', kind: 'path' },
          { key: 'left_on', label: 'Match on (this table)', kind: 'columns' },
          { key: 'right_on', label: 'Matching columns in the other file (if named differently)', kind: 'text', placeholder: 'same names' },
          { key: 'how', label: 'Keep', kind: 'select', options: [['left', 'All rows here (left join)'], ['inner', 'Only matching rows (inner)'], ['full', 'All rows from both (full)'], ['right', 'All rows there (right)'], ['semi', 'Rows here that match (semi)'], ['anti', "Rows here that don't match (anti)"], ['cross', 'Every combination (cross)']] },
          { key: 'suffix', label: 'Suffix for clashing names', kind: 'text', placeholder: '_right' },
        ],
        toParams: p => ({ ...p, right_on: p.right_on ? commaList(p.right_on) : undefined }),
        fromParams: p => ({ ...p, right_on: (p.right_on || []).join(', ') === (p.left_on || []).join(', ') ? '' : (p.right_on || []).join(', ') }),
      },
      {
        type: 'append_rows', label: 'Append rows from file',
        defaults: { how: 'diagonal_relaxed' },
        fields: [
          { key: 'path', label: 'File path', kind: 'path' },
          { key: 'how', label: 'Columns', kind: 'select', options: [['diagonal_relaxed', 'Match by name, fill gaps'], ['vertical_relaxed', 'Must have the same columns']] },
        ],
      },
    ],
  },
  {
    name: 'Time series',
    timeOnly: true,
    ops: [
      {
        type: 'resample', label: 'Resample (time buckets)',
        defaults: { agg: 'mean', freq: '1d' },
        fields: [
          { key: 'time_col', label: 'Time column', kind: 'column', types: 'date' },
          { key: 'freq', label: 'Bucket size (1h, 1d, 1w, 1mo)', kind: 'text' },
          { key: 'val_col', label: 'Value column', kind: 'column', types: 'numeric' },
          { key: 'agg', label: 'Summary', kind: 'select', options: AGGS },
        ],
      },
      {
        type: 'rolling', label: 'Rolling window',
        defaults: { agg: 'mean', window: '7d' },
        fields: [
          { key: 'time_col', label: 'Time column', kind: 'column', types: 'date' },
          { key: 'window', label: 'Window (e.g. 7d)', kind: 'text' },
          { key: 'val_col', label: 'Value column', kind: 'column', types: 'numeric' },
          { key: 'agg', label: 'Summary', kind: 'select', options: [['mean', 'Mean'], ['sum', 'Sum'], ['min', 'Min'], ['max', 'Max'], ['median', 'Median'], ['std', 'Std dev']] },
          { key: 'new_column', label: 'New column name (optional)', kind: 'text' },
        ],
      },
      {
        type: 'fill_gaps', label: 'Fill gaps over time',
        defaults: { method: 'forward' },
        fields: [
          { key: 'time_col', label: 'Time column', kind: 'column', types: 'date' },
          { key: 'val_col', label: 'Column to fill', kind: 'column' },
          { key: 'method', label: 'Fill with', kind: 'select', options: [['forward', 'Previous value'], ['backward', 'Next value']] },
        ],
      },
    ],
  },
  {
    name: 'SQL',
    ops: [
      {
        type: 'sql', label: 'SQL query',
        defaults: { query: 'SELECT *\nFROM df\n', dialect: 'duckdb' },
        fields: [
          { key: 'query', label: 'Query (the current table is "df")', kind: 'textarea', rows: 6 },
          { key: 'dialect', label: 'Engine', kind: 'select', options: [['duckdb', 'DuckDB (full SQL)'], ['polars', 'Polars SQL (streams large data)']] },
        ],
      },
    ],
  },
];

export const AGG_OPTIONS = AGGS;

export const OPS_BY_TYPE = Object.fromEntries(OP_GROUPS.flatMap(g => g.ops.map(o => [o.type, o])));

// Settings a step learned from the data, or stored for exact replay. They are
// re-learned when an edited step is saved, so the form never sends them back.
const LEARNED = ['categories', 'mapping', 'stats', 'bounds', 'source_dtypes', 'dtypes', 'dtype', 'match', 'match_dtypes'];
const firstValue = v => (v && typeof v === 'object' && !Array.isArray(v) ? Object.values(v)[0] : v);
const joinList = v => (Array.isArray(v) ? v.join(', ') : v);

/** Turn a saved step's params back into form state, for editing. */
export function paramsToForm(def, params) {
  let p = { ...params };
  LEARNED.forEach(k => delete p[k]);
  if (def.type === 'filter_rows' && !p.conditions) {
    p = { conditions: [{ column: p.column, operator: p.operator, value: p.value ?? '', value2: p.value2, ignore_case: p.ignore_case }], combine: 'and' };
  }
  if (def.type === 'filter_rows') {
    p.conditions = p.conditions.map(c => {
      const { dtype, ...rest } = c; // eslint-disable-line no-unused-vars
      return { ...rest, value: Array.isArray(rest.value) ? rest.value.join(', ') : rest.value ?? '' };
    });
  }
  if ('find' in p) p.find = firstValue(p.find);
  if ('replace' in p) p.replace = firstValue(p.replace);
  if ('value' in p && def.type === 'fill_missing') p.value = firstValue(p.value);
  ['breaks', 'labels', 'names'].forEach(k => { if (k in p) p[k] = joinList(p[k]); });
  if (def.type === 'clip_outliers' && params.method === 'values' && params.bounds) {
    const b = Object.values(params.bounds)[0] || {};
    p.lower = b.lower ?? undefined;
    p.upper = b.upper ?? undefined;
  }
  if (p.fit_on === null) delete p.fit_on;
  return def.fromParams ? def.fromParams(p) : p;
}

/** Drop hidden and empty fields, then apply the op's own reshaping. */
export function buildParams(def, params) {
  const out = {};
  for (const f of def.fields) {
    if (f.show && !f.show(params)) continue;
    const v = params[f.key];
    if (v === undefined || v === '' || (Array.isArray(v) && v.length === 0 && f.optional)) continue;
    out[f.key] = v;
  }
  for (const k of Object.keys(params)) {
    if (!(k in out) && !def.fields.some(f => f.key === k)) out[k] = params[k];
  }
  if (out.fit_on && !out.fit_on.column) delete out.fit_on;
  const shaped = def.toParams ? def.toParams({ ...params, ...out }) : out;
  if (def.type === 'filter_rows') {
    return {
      conditions: (shaped.conditions || []).filter(c => c.column),
      combine: shaped.combine || params.combine || 'and',
    };
  }
  return shaped;
}
