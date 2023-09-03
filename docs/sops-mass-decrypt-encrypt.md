### sops decrypt all files in working dir (recursive)
```bash
for file in $(find . -name "*.sops.yaml");
do
  sops -d $file > $file.dec;
  mv $file.dec $file
done

```

## sops encrypt all files in working dir (recursive)
```bash
for file in $(find . -name "*.sops.yaml");
do
  sops -e -i $file;
done

```
