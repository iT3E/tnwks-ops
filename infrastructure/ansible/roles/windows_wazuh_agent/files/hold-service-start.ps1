param(
    [Parameter(Mandatory)]
    [string]$PackagePath,
    [Parameter(Mandatory)]
    [string]$TransformPath
)

# The pinned MSI creates WazuhSvc with automatic startup. Hold it disabled
# through installation so a reboot cannot start it before enrollment.
$modifiedPath = Join-Path (Split-Path -Parent $PackagePath) 'transform-input.msi'
Copy-Item -LiteralPath $PackagePath -Destination $modifiedPath -Force
if (Test-Path -LiteralPath $TransformPath) {
    Remove-Item -LiteralPath $TransformPath -Force
}

$installer = New-Object -ComObject WindowsInstaller.Installer
$original = $null
$modified = $null
$view = $null
try {
    $original = $installer.OpenDatabase($PackagePath, 0)
    $modified = $installer.OpenDatabase($modifiedPath, 1)
    $view = $modified.OpenView('UPDATE `ServiceInstall` SET `StartType` = 4 WHERE `Name` = ''WazuhSvc''')
    $view.Execute() | Out-Null
    $view.Close() | Out-Null
    $modified.Commit() | Out-Null
    $null = $modified.GenerateTransform($original, $TransformPath)
    if (-not (Test-Path -LiteralPath $TransformPath -PathType Leaf)) {
        throw 'Windows Installer did not generate the Wazuh service transform'
    }
    # Require a matching product and first three version fields.
    $modified.CreateTransformSummaryInfo($original, $TransformPath, 0, 290)
} finally {
    foreach ($object in @($view, $modified, $original, $installer)) {
        if ($null -ne $object) {
            [Runtime.InteropServices.Marshal]::FinalReleaseComObject($object) | Out-Null
        }
    }
}
