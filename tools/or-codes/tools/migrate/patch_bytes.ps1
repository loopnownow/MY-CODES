# 字节级字面替换：不动文件编码（含 BOM / GBK 中文注释），只替换 ASCII 路径串
# 用法: pwsh -File patch_bytes.ps1 -Path "G:\fyh_CAC\settings.ini" -Old '{lab_root}\fyh_CAC' -New '{lab_root}\pre-submit\fyh_CAC'
param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Old,
    [Parameter(Mandatory = $true)][string]$New
)

$data = [System.IO.File]::ReadAllBytes($Path)
$o = [System.Text.Encoding]::ASCII.GetBytes($Old)
$n = [System.Text.Encoding]::ASCII.GetBytes($New)
$out = New-Object 'System.Collections.Generic.List[byte]'
$i = 0
$cnt = 0
while ($i -lt $data.Length) {
    $m = $false
    if ($i -le ($data.Length - $o.Length)) {
        $m = $true
        for ($j = 0; $j -lt $o.Length; $j++) {
            if ($data[$i + $j] -ne $o[$j]) { $m = $false; break }
        }
    }
    if ($m) {
        foreach ($b in $n) { $out.Add($b) }
        $i += $o.Length
        $cnt++
    }
    else {
        $out.Add($data[$i])
        $i++
    }
}
if ($cnt -gt 0) { [System.IO.File]::WriteAllBytes($Path, $out.ToArray()) }
"{0}`t{1}" -f $Path, $cnt
