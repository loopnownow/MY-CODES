# ==== load/attach the package =================
require(PepsNMR);library(readxl);library(stringr);library(dplyr)
#title  <-  data.frame(read.csv("title.csv"))
# ==== define the path to the data files =================
#setwd("/home/lee/R/mrs")
dir <- getwd()
# ==== read the FIDs and their metadata =================
fidList <- ReadFids(dir,dirs.names = F)
Fid_data0 <- fidList[["Fid_data"]]
Fid_info <- fidList[["Fid_info"]]
dirname <- rownames((ReadFids(dir,dirs.names = T)[["Fid_info"]]))
time <- as.numeric(colnames(Fid_data0))*1000
# ==== GroupDelayCorrection =================
Fid_data.GDC <- GroupDelayCorrection(Fid_data0, Fid_info, verbose = F)
# ====  SolventSuppression =================
SS.res <- SolventSuppression(Fid_data.GDC, returnSolvent = T)
Fid_data.SS <- SS.res[["Fid_data"]]
SolventRe <- SS.res[["SolventRe"]]
# ==== Apodization =================
Fid_data.A <- Apodization(Fid_data.SS, Fid_info)
# ==== Zero Filling =================
Fid_data.ZF <- ZeroFilling(Fid_data.A, fn = ncol(Fid_data.A))
# ==== FourierTransform =================
RawSpect_data.FT <- FourierTransform(Fid_data.ZF, Fid_info)
# ==== ZeroOrderPhaseCorrection =================
Spectrum_data.ZOPC <- ZeroOrderPhaseCorrection(RawSpect_data.FT)
# ==== InternalReferencing =================
target.value <- 0
Spectrum_data.IR <- InternalReferencing(Spectrum_data.ZOPC, Fid_info, ppm.value = target.value)
# ==== BaselineCorrection =================
BC.res <- BaselineCorrection(Spectrum_data.IR, returnBaseline = TRUE,lambda.bc = 1e8, p.bc = 0.01)
Spectrum_data.BC <- BC.res[["Spectrum_data"]] 
Baseline <- BC.res[["Baseline"]]
# ==== NegativeValuesZeroing =================
Spectrum_data.NVZ <- NegativeValuesZeroing(Spectrum_data.BC)
# ==== Warping =================
W.res <- Warping(Spectrum_data.NVZ, returnWarpFunc = TRUE, reference.choice = "fixed")
Spectrum_data.W <- W.res[["Spectrum_data"]]
warp_func <- W.res[["Warp.func"]]
# ==== WindowSelection =================
Spectrum_data.WS <- WindowSelection(Spectrum_data.W, from.ws = 10, to.ws = 0)
# ==== Bucketing =================
Spectrum_data.B <- Bucketing(Spectrum_data.WS, width = F, mb = 1000, intmeth = "t")
# ==== RegionRemoval =================
Spectrum_data.RR <- RegionRemoval(Spectrum_data.B, typeofspectra = "serum")
# ==== ZoneAggregation =================
Spectrum_data <- ZoneAggregation(Spectrum_data.RR)
# ==== Normalization =================
Spectrum_data.N <- Normalization(Spectrum_data, type.norm = "mean")
# # spectra_data <- importSpectraBruker(dir)
# spectra_norm <- data.frame(t(Re(Spectrum_data.N )))
# # spectra_align <- alignSpectra(spectra_norm, verbose = F)
# spectra_obj <- createSpectra(spectra_norm)
# to_exclude <- matrix(c(4.5, 5.1), ncol = 2, byrow = TRUE)
# ASICS_results <- ASICS(spectra_obj, exclusion.areas = to_exclude, verbose = F)
# ==== Final spectra visualisation =================
Draw(Spectrum_data.N[1:2, ], type.draw = c("signal"), subtype = "stacked", output = c("default"))
#setwd("~/R")
data <- data.frame(rownames(Fid_info),dirname,Re(Spectrum_data.N))
#------------------------ID--------------------#
t1 <- data.frame(X1=as.numeric(str_extract(title[,1], "[0-9].[0-9]{2}")), name = str_extract(title[,1], "[a-zA-Z]+"))
t2 <- round(as.numeric(str_extract(colnames(data), "[0-9].[0-9]{3}")),2)[-1:-2]
data1 <- rbind(t2,data[,-1:-2])
data2 <- data.frame(t(data1))
data3 <- left_join(t1,data2,by="X1")
data4<- data.frame(t(data3[,-1]))
colnames(data4)<- data4[1,]
data5<- data.frame(data[,2],data4[-1,])
rownames(data5)<- data[,1]
#------------------------ID--------------------#
write.csv(data5,file = "result.csv",row.names = F)

