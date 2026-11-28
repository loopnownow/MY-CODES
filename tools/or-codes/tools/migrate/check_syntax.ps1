# 只读语法校验：对改过的 .py 做 ast.parse，对 .ps1 做 Parser::ParseFile
# 用法: pwsh -File check_syntax.ps1
$root = '{lab_root}\_Codes'
$pat = 'pre-submit'
$py = Get-ChildItem -LiteralPath $root -Recurse -File -Filter *.py -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\\.git\\|\\site-packages\\|\\__pycache__\\' } |
    Where-Object { (Get-Content -LiteralPath $_.FullName -Raw -ErrorAction SilentlyContinue) -match [regex]::Escape($pat) }
$badPy = 0
foreach ($f in $py) {
    $out = & python -B -c "import ast,sys; ast.parse(open(sys.argv[1],encoding='utf-8',errors='replace').read()); print('OK')" $f.FullName 2>&1
    if ($out -notmatch 'OK') { $badPy++; "PY FAIL: $($f.FullName)`n$out" }
}
"py files checked: $($py.Count), failed: $badPy"

$ps1 = Get-ChildItem -LiteralPath $root -Recurse -File -Filter *.ps1 -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\\.git\\' } |
    Where-Object { (Get-Content -LiteralPath $_.FullName -Raw -ErrorAction SilentlyContinue) -match [regex]::Escape($pat) }
$badPs = 0
foreach ($f in $ps1) {
    $errs = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($f.FullName, [ref]$null, [ref]$errs)
    if ($errs -and $errs.Count -gt 0) { $badPs++; "PS FAIL: $($f.FullName) :: $($errs[0].Message)" }
}
"ps1 files checked: $($ps1.Count), failed: $badPs"
